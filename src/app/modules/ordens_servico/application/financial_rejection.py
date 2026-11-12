"""Conclusão segura da limpeza iniciada por uma recusa financeira."""

from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperationType,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    StatusOrcamentoFinanceiro,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.infrastructure.db.repositories.distributed_operation_repo import (
    PostgresDistributedOperationRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.shared.value_objects.id import ID


async def try_finalize_financial_rejection(
    session: AsyncSession, order_id: UUID
) -> bool:
    """Encerra a OS somente quando nenhuma reserva pode continuar ativa."""

    order_repo = OrdemServicoRepo(session)
    order = await order_repo.obter_por_id_com_lock(ID(order_id))
    if order is None:
        return False
    if order.quote_status is not StatusOrcamentoFinanceiro.RECUSADO:
        return False
    if order.status is StatusOrdemServico.ENCERRADA:
        return True
    if order.status is not StatusOrdemServico.AGUARDANDO_APROVACAO:
        return False
    if await ReservaItemSagaRepo(session).listar_nao_terminais_da_os(order_id):
        return False
    if await PostgresDistributedOperationRepo(session).list_pending_for_order(
        order_id, DistributedOperationType.RELEASE
    ):
        return False
    items = await order_repo.listar_itens_da_os(ID(order_id))
    if any(item.status is not StatusItemNaOS.CANCELADO for item in items):
        return False
    await order_repo.atualizar(order.encerrar_por_recusa_financeira())
    return True
