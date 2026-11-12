"""Marcador persistente mínimo da reconciliação financeira."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class FinancialReconciliationModel(SQLModel, table=True):
    __tablename__ = "financial_reconciliation"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('SYNCHRONIZED', 'UPDATE_REQUIRED', "
            "'TEMPORARILY_UNAVAILABLE', 'INCONSISTENT')",
            name="ck_financial_reconciliation_status",
        ),
        sa.Index(
            "ix_financial_reconciliation_selection",
            "status",
            "last_checked_at",
            "order_id",
        ),
    )

    order_id: UUID = Field(
        sa_column=sa.Column(
            sa.Uuid,
            sa.ForeignKey("ordem_servico.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    status: str = Field(sa_column=sa.Column(sa.String(32), nullable=False))
    reason: str = Field(sa_column=sa.Column(sa.String(128), nullable=False))
    last_checked_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    last_applied_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    last_error: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(512), nullable=True),
    )
    check_count: int = Field(sa_column=sa.Column(sa.Integer, nullable=False, default=1))

