"""Correlaciona a projeção de pagamento ao orçamento autoritativo."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_09_payment_quote"
down_revision = "20261112_08_fin_reject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ordem_servico", sa.Column("payment_quote_id", sa.Uuid()))
    op.create_check_constraint(
        "ck_ordem_payment_projection",
        "ordem_servico",
        "(payment_id IS NULL AND payment_status IS NULL AND payment_amount IS NULL "
        "AND payment_confirmed_at IS NULL AND payment_quote_id IS NULL "
        "AND payment_quote_version IS NULL) OR "
        "(payment_id IS NOT NULL AND payment_status = 'CONFIRMED' "
        "AND payment_amount > 0 AND payment_confirmed_at IS NOT NULL "
        "AND payment_quote_id IS NOT NULL AND payment_quote_version > 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ordem_payment_projection", "ordem_servico", type_="check")
    op.drop_column("ordem_servico", "payment_quote_id")
