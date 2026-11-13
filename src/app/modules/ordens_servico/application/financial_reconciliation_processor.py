"""Aplica um snapshot autoritativo na mesma transação da auditoria local."""

from datetime import UTC, datetime
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.integrations.finance import FinancialStatus
from app.modules.ordens_servico.application.financial_event_handler import (
    FinancialEventHandler,
)
from app.modules.ordens_servico.application.financial_reconciliation import (
    FinancialReconciliationCategory,
    FinancialReconciliationComparison,
    compare_financial_projection,
)
from app.modules.ordens_servico.domain.exceptions import FinancialEventInvalidError
from app.modules.ordens_servico.infrastructure.db.repositories.financial_reconciliation_repo import (
    FinancialReconciliationRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.shared.value_objects.id import ID


class FinancialReconciliationProcessor:
    def __init__(self, applier: FinancialEventHandler | None = None) -> None:
        self._applier = applier or FinancialEventHandler()

    async def process(
        self,
        *,
        order_id: UUID,
        snapshot: FinancialStatus | None,
        correlation_id: UUID,
        session: AsyncSession,
        checked_at: datetime | None = None,
    ) -> FinancialReconciliationComparison:
        now = checked_at or datetime.now(UTC)
        order_repo = OrdemServicoRepo(session)
        order = await order_repo.obter_por_id_com_lock(ID(order_id))
        if order is None:
            raise FinancialEventInvalidError(f"OS '{order_id}' não encontrada.")
        comparison = compare_financial_projection(order, snapshot)
        applied = False
        if comparison.category is FinancialReconciliationCategory.UPDATE_REQUIRED:
            if snapshot is None:  # defesa contra classificação futura incompatível
                raise FinancialEventInvalidError("Atualização sem snapshot autoritativo.")
            updated = await self._applier.apply_authoritative_snapshot(
                snapshot,
                session,
                correlation_id=correlation_id,
            )
            comparison = compare_financial_projection(updated, snapshot)
            if comparison.category is not FinancialReconciliationCategory.SYNCHRONIZED:
                raise FinancialEventInvalidError(
                    f"Snapshot não convergiu após aplicação: {comparison.reason}."
                )
            applied = True
        await FinancialReconciliationRepo(session).record(
            order_id=order_id,
            status=comparison.category,
            reason=comparison.reason,
            checked_at=now,
            applied=applied,
        )
        return comparison

