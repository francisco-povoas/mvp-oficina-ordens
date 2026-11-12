"""Indexa a seleção limitada de ordens elegíveis à reconciliação."""

from alembic import op

revision = "20261112_11_fin_recon_select"
down_revision = "20261112_10_fin_recon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ordem_financial_reconciliation_selection",
        "ordem_servico",
        ["status", "atualizado_em", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ordem_financial_reconciliation_selection",
        table_name="ordem_servico",
    )
