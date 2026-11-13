"""Adiciona o motivo projetado da recusa financeira."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_08_fin_reject"
down_revision = "20261112_07_fin_quote"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ordem_servico", sa.Column("quote_rejection_reason", sa.Text()))
    op.create_check_constraint(
        "ck_ordem_quote_rejection_reason",
        "ordem_servico",
        "quote_rejection_reason IS NULL OR char_length(quote_rejection_reason) <= 512",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ordem_quote_rejection_reason", "ordem_servico", type_="check"
    )
    op.drop_column("ordem_servico", "quote_rejection_reason")
