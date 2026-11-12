"""Inicia o recebimento assíncrono de um item em backorder."""

from dataclasses import dataclass
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
from app.modules.ordens_servico.domain.exceptions import (
    ItemOrdemServicoStatusInvalidoError,
    OrdemServicoItemNaoEncontradoError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoTransicaoInvalidaError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.outbox import OutboxMessage
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


@dataclass(frozen=True)
class ConfirmarRecebimentoItemDaOrdemServicoUseCase:
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
            order = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if order is None:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            item = await self.uow.ordem_servico_repo.obter_item_por_id_com_lock(item_id)
            if item is None or item.ordem_servico_id != os_id.value:
                raise OrdemServicoItemNaoEncontradoError(
                    "Item informado não pertence à ordem de serviço."
                )

            business_key = distributed_operation_business_key(
                DistributedOperationType.RECEIVE,
                ordem_servico_item_id=item.id.value,
            )
            request_hash = distributed_operation_request_hash(
                operation_type=DistributedOperationType.RECEIVE,
                ordem_servico_id=os_id.value,
                ordem_servico_item_id=item.id.value,
                resource_id=item.item_estoque_id,
                quantity=item.quantidade,
                reservation_id=None,
            )
            existing = await self.uow.distributed_operation_repo.get_by_business_key(
                business_key
            )
            if existing:
                existing.validate_repetition(request_hash)
                return distributed_operation_response(existing)

            if order.status is not StatusOrdemServico.AGUARDANDO_ITENS:
                raise OrdemServicoTransicaoInvalidaError(
                    "Só é possível confirmar recebimento de item para OS com status "
                    f"'AGUARDANDO_ITENS'. Status atual: '{order.status.value}'."
                )
            if item.status is not StatusItemNaOS.A_RECEBER:
                raise ItemOrdemServicoStatusInvalidoError(
                    "Só é possível receber item em status 'A_RECEBER'. "
                    f"Status atual: '{item.status.value}'."
                )

            now = datetime.now(UTC)
            operation_id = uuid4()
            message_id = uuid4()
            operation = DistributedOperation.start(
                operation_id=operation_id,
                operation_type=DistributedOperationType.RECEIVE,
                ordem_servico_id=os_id.value,
                ordem_servico_item_id=item.id.value,
                resource_id=item.item_estoque_id,
                quantity=item.quantidade,
                business_key=business_key,
                request_hash=request_hash,
                correlation_id=correlation_id,
                command_message_id=message_id,
                reservation_id=None,
                now=now,
            )
            await self.uow.distributed_operation_repo.save(operation)
            await self.uow.outbox.add(
                OutboxMessage(
                    message_id=message_id,
                    aggregate_type="DistributedOperation",
                    aggregate_id=operation_id,
                    event_type="ReceiveBackorderedItem.v1",
                    event_version=1,
                    payload={
                        "order_id": str(order.id.value),
                        "order_item_id": str(item.id.value),
                        "resource_id": str(item.item_estoque_id),
                        "quantity": item.quantidade,
                    },
                    correlation_id=correlation_id,
                    causation_id=None,
                    saga_id=None,
                    occurred_at=now,
                )
            )
            logger.info(
                "Recebimento coordenado operation_id=%s ordem_id=%s item_id=%s",
                operation_id,
                order.id.value,
                item.id.value,
            )

        return distributed_operation_response(operation)
