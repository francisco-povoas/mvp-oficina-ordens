"""Inicia a remoção idempotente de um item da OS."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    DistributedOperationResponse,
)
from app.modules.ordens_servico.application.use_cases.obter_operacao_distribuida import (
    distributed_operation_response,
)
from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationType,
    distributed_operation_business_key,
    distributed_operation_request_hash,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    ResultadoTerminalReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoItemNaoEncontradoError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoTransicaoInvalidaError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.outbox import OutboxMessage
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


@dataclass(frozen=True)
class RemoverItemDaOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork

    async def execute(
        self,
        ordem_servico_id: str,
        ordem_servico_item_id: str,
        *,
        correlation_id: UUID,
    ) -> DistributedOperationResponse:
        os_id = ID.from_string(ordem_servico_id)
        item_id = ID.from_string(ordem_servico_item_id)

        async with self.uow:
            ordem = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if not ordem:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            item = await self.uow.ordem_servico_repo.obter_item_por_id_com_lock(item_id)
            if not item or item.ordem_servico_id != os_id.value:
                raise OrdemServicoItemNaoEncontradoError(
                    f"Item com ID {ordem_servico_item_id} não encontrado na OS."
                )

            business_key = distributed_operation_business_key(
                DistributedOperationType.RELEASE,
                ordem_servico_item_id=item.id.value,
            )
            saga = await self.uow.reserva_item_saga_repo.obter_por_ordem_servico_item_id(
                item.id.value
            )
            reservation_id = saga.reservation_id if saga else None
            request_hash = distributed_operation_request_hash(
                operation_type=DistributedOperationType.RELEASE,
                ordem_servico_id=os_id.value,
                ordem_servico_item_id=item.id.value,
                resource_id=item.item_estoque_id,
                quantity=item.quantidade,
                reservation_id=reservation_id,
            )
            existing = await self.uow.distributed_operation_repo.get_by_business_key(
                business_key
            )
            if existing:
                existing.validate_repetition(request_hash)
                return distributed_operation_response(existing)

            if ordem.status is not StatusOrdemServico.EM_DIAGNOSTICO:
                raise OrdemServicoTransicaoInvalidaError(
                    "Só é possível remover itens em OS com status "
                    f"'EM_DIAGNOSTICO'. Status atual: '{ordem.status.value}'."
                )
            if item.status is StatusItemNaOS.CANCELADO:
                raise OrdemServicoInvalidaError("O item já foi cancelado.")
            if item.status not in {StatusItemNaOS.RESERVADO, StatusItemNaOS.A_RECEBER}:
                raise OrdemServicoInvalidaError(
                    f"Item em status '{item.status.value}' não pode ser removido."
                )

            now = datetime.now(UTC)
            operation_id = uuid4()
            if item.status is StatusItemNaOS.A_RECEBER:
                operation = DistributedOperation.complete_without_reservation(
                    operation_id=operation_id,
                    ordem_servico_id=os_id.value,
                    ordem_servico_item_id=item.id.value,
                    resource_id=item.item_estoque_id,
                    quantity=item.quantidade,
                    business_key=business_key,
                    request_hash=request_hash,
                    correlation_id=correlation_id,
                    now=now,
                )
                await self.uow.distributed_operation_repo.save(operation)
                await self.uow.ordem_servico_repo.atualizar_item(
                    replace(item, status=StatusItemNaOS.CANCELADO)
                )
                await self.uow.ordem_servico_repo.atualizar(ordem.registrar_alteracao())
            else:
                if (
                    saga is None
                    or saga.terminal_result
                    is not ResultadoTerminalReservaItemSaga.RESERVED
                    or reservation_id is None
                ):
                    raise OrdemServicoInvalidaError(
                        "Item reservado não possui reserva autoritativa persistida."
                    )
                message_id = uuid4()
                operation = DistributedOperation.start(
                    operation_id=operation_id,
                    operation_type=DistributedOperationType.RELEASE,
                    ordem_servico_id=os_id.value,
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
                await self.uow.distributed_operation_repo.save(operation)
                await self.uow.outbox.add(
                    OutboxMessage(
                        message_id=message_id,
                        aggregate_type="DistributedOperation",
                        aggregate_id=operation_id,
                        event_type="ReleaseReservation.v1",
                        event_version=1,
                        payload={
                            "reservation_id": str(reservation_id),
                            "resource_id": str(item.item_estoque_id),
                            "quantity": item.quantidade,
                            "reason": "ITEM_REMOVAL",
                        },
                        correlation_id=correlation_id,
                        causation_id=None,
                        saga_id=None,
                        occurred_at=now,
                    )
                )
            logger.info(
                "Remoção de item coordenada operation_id=%s ordem_id=%s item_id=%s",
                operation_id,
                os_id.value,
                item.id.value,
            )

        return distributed_operation_response(operation)
