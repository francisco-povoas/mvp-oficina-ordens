"""Unit of Work concreto do módulo Ordens de Serviço."""

from app.integrations.resources import ItemEstoquePort
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.shared.infra.db import DBSession
from app.shared.infra.db.base_uow import BaseUnitOfWork


class OrdemServicoUnitOfWork(BaseUnitOfWork):
    """Gerencia a transação que envolve OS e estoque na mesma sessão."""

    def __init__(self, session: DBSession, item_estoque_repo: ItemEstoquePort) -> None:
        super().__init__(session)
        self.ordem_servico_repo = OrdemServicoRepo(session)
        self.item_estoque_repo = item_estoque_repo


def ordem_servico_uow_factory(
    session: DBSession, item_estoque_repo: ItemEstoquePort
) -> OrdemServicoUnitOfWork:
    return OrdemServicoUnitOfWork(session, item_estoque_repo)
