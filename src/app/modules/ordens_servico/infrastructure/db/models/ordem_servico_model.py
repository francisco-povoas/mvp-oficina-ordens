"""Modelo ORM da tabela ordem_servico."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class OrdemServicoModel(SQLModel, table=True):
    """Modelo ORM da tabela de Ordens de Serviço."""

    __tablename__ = "ordem_servico"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('RECEBIDA', 'EM_DIAGNOSTICO', 'DIAGNOSTICO_CONCLUIDO', "
            "'AGUARDANDO_APROVACAO', 'AGUARDANDO_ITENS', 'APROVADA', "
            "'EM_EXECUCAO', 'FINALIZADA', 'ENTREGUE', 'ENCERRADA')",
            name="ck_ordem_status_publico",
        ),
    )

    id: UUID = Field(primary_key=True)
    cliente_id: UUID = Field(
        sa_column=sa.Column(sa.Uuid, sa.ForeignKey("cliente.id"), nullable=False)
    )
    veiculo_id: UUID = Field(
        sa_column=sa.Column(sa.Uuid, sa.ForeignKey("veiculo.id"), nullable=False)
    )
    status: str = Field(sa_column=sa.Column(sa.String(30), nullable=False))
    queixa_inicial: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    diagnostico: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text, nullable=True)
    )
    criado_em: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    atualizado_em: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    iniciado_diagnostico_em: Optional[datetime] = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    diagnostico_concluido_em: Optional[datetime] = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    version: int = Field(default=1, sa_column=sa.Column(sa.Integer, nullable=False))

    quote_id: Optional[UUID] = Field(
        default=None, sa_column=sa.Column(sa.Uuid, nullable=True)
    )
    quote_status: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.String(20), nullable=True)
    )
    quote_version: Optional[int] = Field(
        default=None, sa_column=sa.Column(sa.Integer, nullable=True)
    )
    quote_total: Optional[Decimal] = Field(
        default=None, sa_column=sa.Column(sa.Numeric(precision=12, scale=2), nullable=True)
    )
    quote_communicated_at: Optional[datetime] = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    quote_decided_at: Optional[datetime] = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )

    # Projeção financeira mínima; o agregado de pagamento pertence a Financeiro.
    payment_id: Optional[UUID] = Field(
        default=None, sa_column=sa.Column(sa.Uuid, nullable=True)
    )
    payment_status: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.String(20), nullable=True)
    )
    payment_amount: Optional[Decimal] = Field(
        default=None, sa_column=sa.Column(sa.Numeric(precision=12, scale=2), nullable=True)
    )
    payment_confirmed_at: Optional[datetime] = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    payment_quote_version: Optional[int] = Field(
        default=None, sa_column=sa.Column(sa.Integer, nullable=True)
    )
