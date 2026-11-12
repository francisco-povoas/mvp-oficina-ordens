"""Registra verificações financeiras sem duplicar o agregado autoritativo."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_10_fin_recon"
down_revision = "20261112_09_payment_quote"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_reconciliation",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_applied_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(512)),
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('SYNCHRONIZED', 'UPDATE_REQUIRED', "
            "'TEMPORARILY_UNAVAILABLE', 'INCONSISTENT')",
            name="ck_financial_reconciliation_status",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["ordem_servico.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index(
        "ix_financial_reconciliation_selection",
        "financial_reconciliation",
        ["status", "last_checked_at", "order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_financial_reconciliation_selection",
        table_name="financial_reconciliation",
    )
    op.drop_table("financial_reconciliation")

