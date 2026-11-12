"""Persistência idempotente do resultado de cada verificação financeira."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import select

from app.modules.ordens_servico.application.financial_reconciliation import (
    FinancialReconciliationCategory,
)
from app.modules.ordens_servico.infrastructure.db.models.financial_reconciliation_model import (
    FinancialReconciliationModel,
)
from app.shared.infra.db import DBSession


@dataclass(frozen=True, slots=True)
class FinancialReconciliationCandidate:
    order_id: UUID
    updated_at: datetime


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

    async def list_due(
        self,
        *,
        batch_size: int,
        stale_before: datetime,
        recheck_before: datetime,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> list[FinancialReconciliationCandidate]:
        from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
            OrdemServicoModel,
        )

        eligible_statuses = (
            "DIAGNOSTICO_CONCLUIDO",
            "AGUARDANDO_APROVACAO",
            "AGUARDANDO_ITENS",
            "APROVADA",
            "EM_EXECUCAO",
            "FINALIZADA",
        )
        query = (
            select(OrdemServicoModel.id, OrdemServicoModel.atualizado_em)
            .outerjoin(
                FinancialReconciliationModel,
                FinancialReconciliationModel.order_id == OrdemServicoModel.id,
            )
            .where(
                OrdemServicoModel.status.in_(eligible_statuses),
                OrdemServicoModel.atualizado_em <= stale_before,
                (
                    FinancialReconciliationModel.order_id.is_(None)
                    | (FinancialReconciliationModel.last_checked_at <= recheck_before)
                ),
            )
        )
        if cursor is not None:
            query = query.where(
                (OrdemServicoModel.atualizado_em > cursor[0])
                | (
                    (OrdemServicoModel.atualizado_em == cursor[0])
                    & (OrdemServicoModel.id > cursor[1])
                )
            )
        rows = (
            await self.session.exec(
                query.order_by(
                    OrdemServicoModel.atualizado_em,
                    OrdemServicoModel.id,
                ).limit(batch_size)
            )
        ).all()
        return [
            FinancialReconciliationCandidate(order_id=row[0], updated_at=row[1])
            for row in rows
        ]

