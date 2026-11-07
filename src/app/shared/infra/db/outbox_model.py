"""Estrutura persistente da Outbox; a publicação será implementada em etapa posterior."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class OutboxMessageModel(SQLModel, table=True):
    __tablename__ = "outbox_message"

    id: UUID = Field(primary_key=True)
    aggregate_type: str = Field(sa_column=sa.Column(sa.String(80), nullable=False))
    aggregate_id: UUID = Field(sa_column=sa.Column(sa.Uuid, nullable=False))
    event_type: str = Field(sa_column=sa.Column(sa.String(120), nullable=False))
    event_version: int = Field(sa_column=sa.Column(sa.Integer, nullable=False))
    payload: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    correlation_id: UUID = Field(sa_column=sa.Column(sa.Uuid, nullable=False))
    causation_id: UUID | None = Field(default=None, sa_column=sa.Column(sa.Uuid))
    occurred_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    published_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), index=True)
    )
    attempts: int = Field(
        default=0,
        sa_column=sa.Column(sa.Integer, nullable=False, server_default="0"),
    )
