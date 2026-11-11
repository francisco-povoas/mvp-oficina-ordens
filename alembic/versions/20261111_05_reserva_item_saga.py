"""Cria a persistência da Saga de inclusão e reserva de peça."""

import sqlalchemy as sa
from alembic import op

revision = "20261111_05_reserva_saga"
down_revision = "20261109_04_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reserva_item_saga",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "ordem_servico_id",
            sa.Uuid(),
            sa.ForeignKey("ordem_servico.id"),
            nullable=False,
        ),
        sa.Column("ordem_servico_item_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("estado", sa.String(32), nullable=False),
        sa.Column("comando_pendente", sa.String(32)),
        sa.Column("ultimo_comando_message_id", sa.Uuid()),
        sa.Column("terminal_result", sa.String(32)),
        sa.Column("reservation_id", sa.Uuid()),
        sa.Column("rejection_code", sa.String(32)),
        sa.Column("rejection_detail", sa.String(512)),
        sa.Column("ultimo_resultado_type", sa.String(80)),
        sa.Column("ultimo_resultado_message_id", sa.Uuid(), unique=True),
        sa.Column("ultimo_resultado_em", sa.DateTime(timezone=True)),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("resource_name", sa.String(200)),
        sa.Column("resource_description", sa.String(1000)),
        sa.Column("resource_unit_price", sa.Numeric(12, 2)),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("quantity > 0", name="ck_reserva_saga_quantity_positiva"),
        sa.CheckConstraint("version > 0", name="ck_reserva_saga_version_positiva"),
        sa.CheckConstraint(
            "estado IN ('PENDING', 'CANCEL_REQUESTED', 'RELEASING', 'COMPLETED', "
            "'COMPENSATED', 'REJECTED', 'RECONCILIATION_REQUIRED')",
            name="ck_reserva_saga_estado",
        ),
        sa.CheckConstraint(
            "comando_pendente IS NULL OR comando_pendente IN "
            "('RESERVE_ITEM', 'RELEASE_RESERVATION')",
            name="ck_reserva_saga_comando",
        ),
        sa.CheckConstraint(
            "terminal_result IS NULL OR terminal_result IN "
            "('RESERVED', 'BACKORDERED', 'NO_RESERVATION', "
            "'RESERVATION_RELEASED', 'RESERVATION_REJECTED')",
            name="ck_reserva_saga_resultado_terminal",
        ),
        sa.CheckConstraint(
            "rejection_code IS NULL OR rejection_code IN "
            "('RESOURCE_NOT_FOUND', 'INVALID_QUANTITY')",
            name="ck_reserva_saga_rejeicao",
        ),
        sa.CheckConstraint(
            "((estado IN ('COMPLETED', 'COMPENSATED', 'REJECTED')) "
            "AND terminal_result IS NOT NULL AND terminal_at IS NOT NULL "
            "AND comando_pendente IS NULL) OR "
            "((estado NOT IN ('COMPLETED', 'COMPENSATED', 'REJECTED')) "
            "AND terminal_result IS NULL AND terminal_at IS NULL)",
            name="ck_reserva_saga_terminal",
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 128",
            name="ck_reserva_saga_idempotency_key",
        ),
        sa.CheckConstraint(
            "char_length(request_hash) = 64",
            name="ck_reserva_saga_request_hash",
        ),
        sa.CheckConstraint(
            "resource_unit_price IS NULL OR resource_unit_price >= 0",
            name="ck_reserva_saga_snapshot_valor",
        ),
        sa.UniqueConstraint(
            "ordem_servico_id",
            "idempotency_key",
            name="uq_reserva_saga_ordem_idempotency_key",
        ),
    )
    op.create_index(
        "ix_reserva_item_saga_operation_id",
        "reserva_item_saga",
        ["operation_id"],
        unique=True,
    )
    op.create_index(
        "ix_reserva_item_saga_ordem_servico_id",
        "reserva_item_saga",
        ["ordem_servico_id"],
    )
    op.create_index(
        "ix_reserva_item_saga_resource_id",
        "reserva_item_saga",
        ["resource_id"],
    )
    op.create_index(
        "ix_reserva_item_saga_estado",
        "reserva_item_saga",
        ["estado"],
    )
    op.create_index(
        "ix_reserva_item_saga_reservation_id",
        "reserva_item_saga",
        ["reservation_id"],
    )


def downgrade() -> None:
    op.drop_table("reserva_item_saga")
