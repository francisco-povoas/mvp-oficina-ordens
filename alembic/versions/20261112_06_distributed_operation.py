"""Cria coordenações persistentes leves para operações de estoque."""

import sqlalchemy as sa
from alembic import op

revision = "20261112_06_dist_op"
down_revision = "20261111_05_reserva_saga"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "distributed_operation",
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("operation_type", sa.String(16), nullable=False),
        sa.Column(
            "ordem_servico_id",
            sa.Uuid(),
            sa.ForeignKey("ordem_servico.id"),
            nullable=False,
        ),
        sa.Column(
            "ordem_servico_item_id",
            sa.Uuid(),
            sa.ForeignKey("ordem_servico_item.id"),
            nullable=False,
        ),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("business_key", sa.String(220), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("command_type", sa.String(80)),
        sa.Column("command_message_id", sa.Uuid(), unique=True),
        sa.Column("command_created_at", sa.DateTime(timezone=True)),
        sa.Column("reservation_id", sa.Uuid()),
        sa.Column("result_type", sa.String(80)),
        sa.Column("result_message_id", sa.Uuid(), unique=True),
        sa.Column("result_received_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_result", sa.String(40)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("quantity > 0", name="ck_distributed_operation_quantity"),
        sa.CheckConstraint("version > 0", name="ck_distributed_operation_version"),
        sa.CheckConstraint(
            "operation_type IN ('RELEASE', 'RECEIVE', 'CONSUME')",
            name="ck_distributed_operation_type",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'COMPLETED')",
            name="ck_distributed_operation_state",
        ),
        sa.CheckConstraint(
            "terminal_result IS NULL OR terminal_result IN "
            "('NO_RESERVATION', 'RESERVATION_RELEASED', "
            "'BACKORDERED_ITEM_RECEIVED', 'RESERVATION_CONSUMED')",
            name="ck_distributed_operation_terminal_result",
        ),
        sa.CheckConstraint(
            "char_length(request_hash) = 64",
            name="ck_distributed_operation_request_hash",
        ),
        sa.CheckConstraint(
            "char_length(business_key) BETWEEN 1 AND 220",
            name="ck_distributed_operation_business_key",
        ),
        sa.CheckConstraint(
            "(state = 'PENDING' AND command_type IS NOT NULL "
            "AND command_message_id IS NOT NULL AND command_created_at IS NOT NULL "
            "AND terminal_result IS NULL AND terminal_at IS NULL) OR "
            "(state = 'COMPLETED' AND terminal_result IS NOT NULL "
            "AND terminal_at IS NOT NULL)",
            name="ck_distributed_operation_lifecycle",
        ),
        sa.CheckConstraint(
            "(result_type IS NULL AND result_message_id IS NULL "
            "AND result_received_at IS NULL) OR "
            "(result_type IS NOT NULL AND result_message_id IS NOT NULL "
            "AND result_received_at IS NOT NULL)",
            name="ck_distributed_operation_result_metadata",
        ),
        sa.CheckConstraint(
            "(operation_type = 'RELEASE' AND "
            "(command_type IS NULL OR command_type = 'ReleaseReservation')) OR "
            "(operation_type = 'RECEIVE' AND command_type = 'ReceiveBackorderedItem') OR "
            "(operation_type = 'CONSUME' AND command_type = 'ConsumeReservation')",
            name="ck_distributed_operation_command_contract",
        ),
        sa.CheckConstraint(
            "result_type IS NULL OR "
            "(operation_type = 'RELEASE' AND result_type = 'ReservationReleased') OR "
            "(operation_type = 'RECEIVE' AND result_type = 'BackorderedItemReceived') OR "
            "(operation_type = 'CONSUME' AND result_type = 'ReservationConsumed')",
            name="ck_distributed_operation_result_contract",
        ),
        sa.CheckConstraint(
            "(terminal_result IS NULL) OR "
            "(terminal_result = 'NO_RESERVATION' AND operation_type = 'RELEASE' "
            "AND command_type IS NULL AND command_message_id IS NULL "
            "AND command_created_at IS NULL AND reservation_id IS NULL "
            "AND result_type IS NULL AND result_message_id IS NULL "
            "AND result_received_at IS NULL) OR "
            "(terminal_result = 'RESERVATION_RELEASED' AND operation_type = 'RELEASE' "
            "AND reservation_id IS NOT NULL AND result_type = 'ReservationReleased') OR "
            "(terminal_result = 'BACKORDERED_ITEM_RECEIVED' AND operation_type = 'RECEIVE' "
            "AND reservation_id IS NOT NULL "
            "AND result_type = 'BackorderedItemReceived') OR "
            "(terminal_result = 'RESERVATION_CONSUMED' AND operation_type = 'CONSUME' "
            "AND reservation_id IS NOT NULL AND result_type = 'ReservationConsumed')",
            name="ck_distributed_operation_terminal_contract",
        ),
    )
    op.create_index(
        "ix_distributed_operation_operation_type",
        "distributed_operation",
        ["operation_type"],
    )
    op.create_index(
        "ix_distributed_operation_ordem_servico_id",
        "distributed_operation",
        ["ordem_servico_id"],
    )
    op.create_index(
        "ix_distributed_operation_ordem_servico_item_id",
        "distributed_operation",
        ["ordem_servico_item_id"],
    )
    op.create_index(
        "ix_distributed_operation_resource_id",
        "distributed_operation",
        ["resource_id"],
    )
    op.create_index(
        "ix_distributed_operation_state",
        "distributed_operation",
        ["state"],
    )
    op.create_index(
        "ix_distributed_operation_reservation_id",
        "distributed_operation",
        ["reservation_id"],
    )


def downgrade() -> None:
    op.drop_table("distributed_operation")
