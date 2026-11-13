"""Porta de persistência das coordenações distribuídas leves."""

from typing import Protocol
from uuid import UUID

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationType,
)


class DistributedOperationRepo(Protocol):
    async def save(self, operation: DistributedOperation) -> None: ...

    async def get_by_operation_id(
        self, operation_id: UUID
    ) -> DistributedOperation | None: ...

    async def get_by_operation_id_for_update(
        self, operation_id: UUID
    ) -> DistributedOperation | None: ...

    async def get_by_business_key(
        self, business_key: str
    ) -> DistributedOperation | None: ...

    async def list_pending_for_order(
        self,
        ordem_servico_id: UUID,
        operation_type: DistributedOperationType | None = None,
    ) -> list[DistributedOperation]: ...

    async def get_latest_completed_for_item(
        self,
        ordem_servico_item_id: UUID,
        operation_type: DistributedOperationType,
    ) -> DistributedOperation | None: ...

    async def update(self, operation: DistributedOperation) -> DistributedOperation: ...
