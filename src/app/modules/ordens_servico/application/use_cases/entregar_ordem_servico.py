"""Coordena consumo de reservas e entrega sem criar uma segunda Saga."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.integrations.finance import FinanceStatusPort
from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    DeliveryOperationResponse,
)
from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationState,
    DistributedOperationType,
    distributed_operation_business_key,
    distributed_operation_request_hash,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    ResultadoTerminalReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoPagamentoNaoRegistradoError,
    OrdemServicoPossuiItemPendenteError,
    OrdemServicoPossuiItemReservadoError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.outbox import OutboxMessage
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


def _delivery_response(
    *,
    order_id: UUID,
    order_status: StatusOrdemServico,
    operations: list[DistributedOperation],
) -> DeliveryOperationResponse:
    pending = any(
        operation.state is DistributedOperationState.PENDING
        for operation in operations
    )
    return DeliveryOperationResponse(
        ordem_servico_id=str(order_id),
        order_status=order_status.value,
        state="PENDING" if pending else "COMPLETED",
        operation_ids=[str(operation.operation_id) for operation in operations],
        status_url=f"/api/v1/ordens-servico/{order_id}",
    )


@dataclass(frozen=True)
class EntregarOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork
    financeiro: FinanceStatusPort | None = None

    async def execute(
        self,
        ordem_servico_id: str,
        *,
        correlation_id: UUID,
    ) -> DeliveryOperationResponse:
        os_id = ID.from_string(ordem_servico_id)

        async with self.uow:
            order = await self.uow.ordem_servico_repo.obter_por_id_com_lock(os_id)
            if order is None:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            items = await self.uow.ordem_servico_repo.listar_itens_da_os(os_id)
            if order.status is StatusOrdemServico.ENTREGUE:
                operations = await self._completed_consumptions(items)
                return _delivery_response(
                    order_id=order.id.value,
                    order_status=order.status,
                    operations=operations,
                )

            order = await self._ensure_payment(order)
            # Executa todas as invariantes de entrega, mas não persiste ENTREGUE antes
            # das confirmações físicas.
            delivered_order = order.entregar()
            self._validate_item_states(items)

            now = datetime.now(UTC)
            operations: list[DistributedOperation] = []
            for item in items:
                if item.status not in {StatusItemNaOS.EM_USO, StatusItemNaOS.CONSUMIDO}:
                    continue
                existing = (
                    await self.uow.distributed_operation_repo.get_latest_completed_for_item(
                        item.id.value,
                        DistributedOperationType.CONSUME,
                    )
                )
                if (
                    existing is not None
                    and item.status is StatusItemNaOS.EM_USO
                ):
                    raise OrdemServicoInvalidaError(
                        "Consumo concluído não foi refletido no estado do item."
                    )
                if existing is None:
                    reservation_id = await self._reservation_id(item)
                    business_key = distributed_operation_business_key(
                        DistributedOperationType.CONSUME,
                        ordem_servico_item_id=item.id.value,
                        reservation_id=reservation_id,
                    )
                    request_hash = distributed_operation_request_hash(
                        operation_type=DistributedOperationType.CONSUME,
                        ordem_servico_id=order.id.value,
                        ordem_servico_item_id=item.id.value,
                        resource_id=item.item_estoque_id,
                        quantity=item.quantidade,
                        reservation_id=reservation_id,
                    )
                    existing = (
                        await self.uow.distributed_operation_repo.get_by_business_key(
                            business_key
                        )
                    )
                    if existing:
                        existing.validate_repetition(request_hash)
                    elif item.status is StatusItemNaOS.CONSUMIDO:
                        raise OrdemServicoInvalidaError(
                            "Item consumido não possui coordenação persistida."
                        )
                    else:
                        message_id = uuid4()
                        existing = DistributedOperation.start(
                            operation_id=uuid4(),
                            operation_type=DistributedOperationType.CONSUME,
                            ordem_servico_id=order.id.value,
                            ordem_servico_item_id=item.id.value,
                            resource_id=item.item_estoque_id,
                            quantity=item.quantidade,
                            business_key=business_key,
                            request_hash=request_hash,
                            correlation_id=correlation_id,
                            command_message_id=message_id,
                            reservation_id=reservation_id,
                            now=now,
                        )
                        await self.uow.distributed_operation_repo.save(existing)
                        await self.uow.outbox.add(
                            OutboxMessage(
                                message_id=message_id,
                                aggregate_type="DistributedOperation",
                                aggregate_id=existing.operation_id,
                                event_type="ConsumeReservation.v1",
                                event_version=1,
                                payload={
                                    "order_id": str(order.id.value),
                                    "order_item_id": str(item.id.value),
                                    "reservation_id": str(reservation_id),
                                    "resource_id": str(item.item_estoque_id),
                                    "quantity": item.quantidade,
                                },
                                correlation_id=correlation_id,
                                causation_id=None,
                                saga_id=None,
                                occurred_at=now,
                            )
                        )
                operations.append(existing)

            if not any(
                operation.state is DistributedOperationState.PENDING
                for operation in operations
            ):
                await self.uow.ordem_servico_repo.atualizar(delivered_order)
                order = delivered_order
            logger.info(
                "Entrega coordenada ordem_id=%s consumos=%s pendentes=%s",
                order.id.value,
                len(operations),
                sum(
                    operation.state is DistributedOperationState.PENDING
                    for operation in operations
                ),
            )

        return _delivery_response(
            order_id=order.id.value,
            order_status=order.status,
            operations=operations,
        )

    async def _ensure_payment(self, order):
        if order.payment_status == "CONFIRMED" and order.payment_id is not None:
            return order
        if self.financeiro is None:
            raise OrdemServicoPagamentoNaoRegistradoError(
                "Não é possível entregar OS sem pagamento confirmado."
            )
        financial_status = await self.financeiro.obter_status(order.id.value)
        payment = financial_status.payment if financial_status else None
        quote = financial_status.quote if financial_status else None
        if (
            payment is None
            or quote is None
            or payment.status != "CONFIRMED"
            or payment.confirmed_at is None
            or quote.status != "APROVADO"
        ):
            raise OrdemServicoPagamentoNaoRegistradoError(
                "Não é possível entregar OS sem confirmação financeira válida."
            )
        updated = order.com_pagamento_confirmado(
            payment_id=payment.payment_id,
            amount=payment.amount,
            confirmed_at=payment.confirmed_at,
            quote_version=quote.quote_version,
        )
        await self.uow.ordem_servico_repo.atualizar(updated)
        return updated

    @staticmethod
    def _validate_item_states(items: list[OrdemServicoItem]) -> None:
        for item in items:
            if item.status is StatusItemNaOS.A_RECEBER:
                raise OrdemServicoPossuiItemPendenteError(
                    "Não é possível entregar OS com item ativo em status 'A_RECEBER'."
                )
            if item.status is StatusItemNaOS.RESERVADO:
                raise OrdemServicoPossuiItemReservadoError(
                    "Não é possível entregar OS com item ativo em status 'RESERVADO'."
                )

    async def _reservation_id(self, item: OrdemServicoItem) -> UUID:
        received = (
            await self.uow.distributed_operation_repo.get_latest_completed_for_item(
                item.id.value,
                DistributedOperationType.RECEIVE,
            )
        )
        if received and received.reservation_id:
            return received.reservation_id
        saga = await self.uow.reserva_item_saga_repo.obter_por_ordem_servico_item_id(
            item.id.value
        )
        if (
            saga
            and saga.terminal_result is ResultadoTerminalReservaItemSaga.RESERVED
            and saga.reservation_id
        ):
            return saga.reservation_id
        raise OrdemServicoInvalidaError(
            f"Item '{item.id}' não possui reserva autoritativa para consumo."
        )

    async def _completed_consumptions(
        self, items: list[OrdemServicoItem]
    ) -> list[DistributedOperation]:
        operations = []
        for item in items:
            if item.status is not StatusItemNaOS.CONSUMIDO:
                continue
            operation = (
                await self.uow.distributed_operation_repo.get_latest_completed_for_item(
                    item.id.value,
                    DistributedOperationType.CONSUME,
                )
            )
            if operation:
                operations.append(operation)
        return operations
