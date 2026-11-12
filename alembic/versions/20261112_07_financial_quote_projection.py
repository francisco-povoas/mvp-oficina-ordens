"""Adiciona a projeção mínima dos fatos de orçamento em Ordens."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_07_fin_quote"
down_revision = "20261112_06_dist_op"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ordem_servico", sa.Column("quote_id", sa.Uuid()))
    op.add_column("ordem_servico", sa.Column("quote_status", sa.String(20)))
    op.add_column("ordem_servico", sa.Column("quote_version", sa.Integer()))
    op.add_column("ordem_servico", sa.Column("quote_total", sa.Numeric(12, 2)))
    op.add_column(
        "ordem_servico", sa.Column("quote_communicated_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "ordem_servico", sa.Column("quote_decided_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_ordem_quote_status",
        "ordem_servico",
        "quote_status IS NULL OR quote_status IN ('COMUNICADO', 'APROVADO', 'RECUSADO')",
    )
    op.create_check_constraint(
        "ck_ordem_quote_version",
        "ordem_servico",
        "quote_version IS NULL OR quote_version > 0",
    )
    op.create_check_constraint(
        "ck_ordem_quote_total",
        "ordem_servico",
        "quote_total IS NULL OR quote_total >= 0",
    )
    op.create_check_constraint(
        "ck_ordem_quote_identity",
        "ordem_servico",
        "(quote_id IS NULL AND quote_status IS NULL AND quote_version IS NULL) OR "
        "(quote_id IS NOT NULL AND quote_status IS NOT NULL AND quote_version IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ordem_quote_identity", "ordem_servico", type_="check")
    op.drop_constraint("ck_ordem_quote_total", "ordem_servico", type_="check")
    op.drop_constraint("ck_ordem_quote_version", "ordem_servico", type_="check")
    op.drop_constraint("ck_ordem_quote_status", "ordem_servico", type_="check")
    op.drop_column("ordem_servico", "quote_decided_at")
    op.drop_column("ordem_servico", "quote_communicated_at")
    op.drop_column("ordem_servico", "quote_total")
    op.drop_column("ordem_servico", "quote_version")
    op.drop_column("ordem_servico", "quote_status")
    op.drop_column("ordem_servico", "quote_id")
