"""Adiciona lease, retry e falhas a Outbox existente."""

import sqlalchemy as sa

from alembic import op

revision = "20261108_02_outbox"
down_revision = "20261107_01_ordens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_message",
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column("outbox_message", sa.Column("claim_id", sa.Uuid()))
    op.add_column("outbox_message", sa.Column("claimed_by", sa.String(120)))
    op.add_column("outbox_message", sa.Column("claimed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "outbox_message",
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column("outbox_message", sa.Column("last_error", sa.Text()))
    op.add_column(
        "outbox_message",
        sa.Column("last_failed_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_outbox_claim_completo",
        "outbox_message",
        "(claim_id IS NULL AND claimed_by IS NULL AND claimed_at IS NULL "
        "AND claim_expires_at IS NULL) OR "
        "(claim_id IS NOT NULL AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL "
        "AND claim_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_outbox_claim_expiracao_valida",
        "outbox_message",
        "claim_expires_at IS NULL OR claim_expires_at > claimed_at",
    )
    op.create_check_constraint(
        "ck_outbox_falha_com_erro",
        "outbox_message",
        "last_failed_at IS NULL OR last_error IS NOT NULL",
    )
    op.create_index(
        "ix_outbox_claimable",
        "outbox_message",
        ["next_attempt_at", "claim_expires_at", "occurred_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_claimable", table_name="outbox_message")
    op.drop_constraint("ck_outbox_falha_com_erro", "outbox_message", type_="check")
    op.drop_constraint(
        "ck_outbox_claim_expiracao_valida", "outbox_message", type_="check"
    )
    op.drop_constraint("ck_outbox_claim_completo", "outbox_message", type_="check")
    op.drop_column("outbox_message", "last_failed_at")
    op.drop_column("outbox_message", "last_error")
    op.drop_column("outbox_message", "claim_expires_at")
    op.drop_column("outbox_message", "claimed_at")
    op.drop_column("outbox_message", "claimed_by")
    op.drop_column("outbox_message", "claim_id")
    op.drop_column("outbox_message", "next_attempt_at")
