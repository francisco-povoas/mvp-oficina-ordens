"""Persistência idempotente do resultado de cada verificação financeira."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert

from app.modules.ordens_servico.application.financial_reconciliation import (
    FinancialReconciliationCategory,
)
from app.modules.ordens_servico.infrastructure.db.models.financial_reconciliation_model import (
    FinancialReconciliationModel,
)
from app.shared.infra.db import DBSession


class FinancialReconciliationRepo:
    def __init__(self, session: DBSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        order_id: UUID,
        status: FinancialReconciliationCategory,
        reason: str,
        checked_at: datetime,
        applied: bool = False,
        error: str | None = None,
    ) -> None:
        statement = insert(FinancialReconciliationModel).values(
            order_id=order_id,
            status=status.value,
            reason=reason,
            last_checked_at=checked_at,
            last_applied_at=checked_at if applied else None,
            last_error=error,
            check_count=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[FinancialReconciliationModel.order_id],
            set_={
                "status": status.value,
                "reason": reason,
                "last_checked_at": checked_at,
                "last_applied_at": (
                    checked_at
                    if applied
                    else FinancialReconciliationModel.last_applied_at
                ),
                "last_error": error,
                "check_count": FinancialReconciliationModel.check_count + 1,
            },
        )
        await self.session.exec(statement)

    async def get(self, order_id: UUID) -> FinancialReconciliationModel | None:
        return await self.session.get(FinancialReconciliationModel, order_id)

