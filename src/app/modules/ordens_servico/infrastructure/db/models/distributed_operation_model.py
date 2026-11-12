"""Modelo PostgreSQL das coordenações distribuídas fora da Saga."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class DistributedOperationModel(SQLModel, table=True):
    __tablename__ = "distributed_operation"
    __table_args__ = (
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

    operation_id: UUID = Field(primary_key=True)
    operation_type: str = Field(max_length=16, index=True)
    ordem_servico_id: UUID = Field(
        sa_column=sa.Column(
            sa.Uuid,
            sa.ForeignKey("ordem_servico.id"),
            nullable=False,
            index=True,
        )
    )
    ordem_servico_item_id: UUID = Field(
        sa_column=sa.Column(
            sa.Uuid,
            sa.ForeignKey("ordem_servico_item.id"),
            nullable=False,
            index=True,
        )
    )
    resource_id: UUID = Field(index=True)
    quantity: int
    business_key: str = Field(unique=True, max_length=220)
    request_hash: str = Field(max_length=64)
    correlation_id: UUID
    state: str = Field(max_length=16, index=True)
    command_type: str | None = Field(default=None, max_length=80)
    command_message_id: UUID | None = Field(default=None, unique=True)
    command_created_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True))
    )
    reservation_id: UUID | None = Field(default=None, index=True)
    result_type: str | None = Field(default=None, max_length=80)
    result_message_id: UUID | None = Field(default=None, unique=True)
    result_received_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True))
    )
    terminal_result: str | None = Field(default=None, max_length=40)
    terminal_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True))
    )
    created_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    version: int = Field(default=1)
