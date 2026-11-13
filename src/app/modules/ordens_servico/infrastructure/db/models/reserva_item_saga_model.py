"""Modelo PostgreSQL da Saga de inclusão e reserva de peça."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class ReservaItemSagaModel(SQLModel, table=True):
    __tablename__ = "reserva_item_saga"
    __table_args__ = (
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

    id: UUID = Field(primary_key=True)
    operation_id: UUID = Field(unique=True, index=True)
    ordem_servico_id: UUID = Field(
        sa_column=sa.Column(
            sa.Uuid,
            sa.ForeignKey("ordem_servico.id"),
            nullable=False,
            index=True,
        )
    )
    ordem_servico_item_id: UUID = Field(unique=True)
    resource_id: UUID = Field(index=True)
    quantity: int
    correlation_id: UUID
    idempotency_key: str = Field(max_length=128)
    request_hash: str = Field(max_length=64)
    estado: str = Field(max_length=32, index=True)
    comando_pendente: str | None = Field(default=None, max_length=32)
    ultimo_comando_message_id: UUID | None = None
    terminal_result: str | None = Field(default=None, max_length=32)
    reservation_id: UUID | None = Field(default=None, index=True)
    rejection_code: str | None = Field(default=None, max_length=32)
    rejection_detail: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(512)),
    )
    ultimo_resultado_type: str | None = Field(default=None, max_length=80)
    ultimo_resultado_message_id: UUID | None = Field(default=None, unique=True)
    ultimo_resultado_em: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    cancellation_requested_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    terminal_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True)),
    )
    resource_name: str | None = Field(default=None, max_length=200)
    resource_description: str | None = Field(
        default=None,
        sa_column=sa.Column(sa.String(1000)),
    )
    resource_unit_price: Decimal | None = Field(
        default=None,
        sa_column=sa.Column(sa.Numeric(12, 2)),
    )
    criado_em: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False))
    atualizado_em: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    version: int = Field(default=1)
