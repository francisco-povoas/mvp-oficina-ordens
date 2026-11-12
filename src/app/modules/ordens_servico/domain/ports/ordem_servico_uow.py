"""Contrato do Unit of Work do módulo Ordens de Serviço."""

from typing import Protocol

from app.modules.ordens_servico.domain.ports.distributed_operation_repo import (
    DistributedOperationRepo,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_repo import OrdemServicoRepo
from app.modules.ordens_servico.domain.ports.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.shared.outbox import OutboxRepository


class OrdemServicoUnitOfWork(Protocol):
    """Agrupa somente persistência pertencente ao serviço de Ordens."""

    ordem_servico_repo: OrdemServicoRepo
    reserva_item_saga_repo: ReservaItemSagaRepo
    distributed_operation_repo: DistributedOperationRepo
    outbox: OutboxRepository

    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc_val, exc_tb): ...
    async def commit(self): ...
    async def rollback(self): ...
