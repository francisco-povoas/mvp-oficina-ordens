"""Unit of Work concreto do módulo Ordens de Serviço."""

from app.modules.ordens_servico.infrastructure.db.repositories.distributed_operation_repo import (
    PostgresDistributedOperationRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.shared.infra.db import DBSession
from app.shared.infra.db.base_uow import BaseUnitOfWork


class OrdemServicoUnitOfWork(BaseUnitOfWork):
    """Gerencia exclusivamente a transação PostgreSQL local de Ordens."""

    def __init__(self, session: DBSession) -> None:
        super().__init__(session)
        self.ordem_servico_repo = OrdemServicoRepo(session)
        self.reserva_item_saga_repo = ReservaItemSagaRepo(session)
        self.distributed_operation_repo = PostgresDistributedOperationRepo(session)


def ordem_servico_uow_factory(session: DBSession) -> OrdemServicoUnitOfWork:
    return OrdemServicoUnitOfWork(session)
