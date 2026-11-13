"""Consulta uma operação assíncrona de inclusão pelo identificador público."""

from dataclasses import dataclass
from uuid import UUID

from app.modules.ordens_servico.application.dtos.ordem_servico import (
    ReservaItemSagaResponse,
)
from app.modules.ordens_servico.application.use_cases.adicionar_item_na_ordem_servico import (
    saga_response,
)
from app.modules.ordens_servico.domain.exceptions import ReservaItemSagaNaoEncontradaError
from app.modules.ordens_servico.domain.ports.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)


@dataclass(frozen=True)
class ObterReservaItemSagaUseCase:
    repo: ReservaItemSagaRepo

    async def execute(
        self,
        ordem_servico_id: UUID,
        operation_id: UUID,
    ) -> ReservaItemSagaResponse:
        saga = await self.repo.obter_por_operation_id(operation_id)
        if saga is None or saga.ordem_servico_id != ordem_servico_id:
            raise ReservaItemSagaNaoEncontradaError("Operação de reserva não encontrada.")
        return saga_response(saga)
