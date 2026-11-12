"""Consulta pública de uma coordenação distribuída de estoque."""

from dataclasses import dataclass
from uuid import UUID

from app.modules.ordens_servico.application.dtos.ordem_servico import (
    DistributedOperationResponse,
)
from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
)
from app.modules.ordens_servico.domain.exceptions import DistributedOperationNotFoundError
from app.modules.ordens_servico.domain.ports.distributed_operation_repo import (
    DistributedOperationRepo,
)


def distributed_operation_response(
    operation: DistributedOperation,
) -> DistributedOperationResponse:
    return DistributedOperationResponse(
        operation_id=str(operation.operation_id),
        operation_type=operation.operation_type.value,
        ordem_servico_item_id=str(operation.ordem_servico_item_id),
        state=operation.state.value,
        terminal_result=(
            operation.terminal_result.value if operation.terminal_result else None
        ),
        reservation_id=(
            str(operation.reservation_id) if operation.reservation_id else None
        ),
        status_url=(
            f"/api/v1/ordens-servico/{operation.ordem_servico_id}/"
            f"itens/operacoes-estoque/{operation.operation_id}"
        ),
    )


@dataclass(frozen=True)
class ObterDistributedOperationUseCase:
    repo: DistributedOperationRepo

    async def execute(
        self,
        ordem_servico_id: UUID,
        operation_id: UUID,
    ) -> DistributedOperationResponse:
        operation = await self.repo.get_by_operation_id(operation_id)
        if operation is None or operation.ordem_servico_id != ordem_servico_id:
            raise DistributedOperationNotFoundError(
                "Operação distribuída de estoque não encontrada."
            )
        return distributed_operation_response(operation)
