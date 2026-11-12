"""Persiste a intenção idempotente de cancelar uma inclusão ainda pendente."""

from dataclasses import dataclass
from uuid import UUID

from app.modules.ordens_servico.application.dtos.ordem_servico import (
    ReservaItemSagaResponse,
)
from app.modules.ordens_servico.application.use_cases.adicionar_item_na_ordem_servico import (
    saga_response,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    EstadoReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoNaoEncontradaError,
    ReservaItemSagaNaoEncontradaError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.shared.value_objects.id import ID


@dataclass(frozen=True)
class CancelarReservaItemSagaUseCase:
    uow: OrdemServicoUnitOfWork

    async def execute(
        self, ordem_servico_id: UUID, operation_id: UUID
    ) -> ReservaItemSagaResponse:
        async with self.uow:
            order = await self.uow.ordem_servico_repo.obter_por_id(
                ID(ordem_servico_id)
            )
            if order is None:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            saga = (
                await self.uow.reserva_item_saga_repo.obter_por_operation_id_com_lock(
                    operation_id
                )
            )
            if saga is None or saga.ordem_servico_id != ordem_servico_id:
                raise ReservaItemSagaNaoEncontradaError(
                    f"Operação '{operation_id}' não encontrada na OS informada."
                )

            updated = saga.solicitar_cancelamento()
            if (
                updated.estado is EstadoReservaItemSaga.CANCEL_REQUESTED
                and updated.version != saga.version
            ):
                await self.uow.reserva_item_saga_repo.atualizar(updated)
            saga = updated

        return saga_response(saga)
