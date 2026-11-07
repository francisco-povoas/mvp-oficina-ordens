"""Modelo ORM da tabela ordem_servico_item."""

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class OrdemServicoItemModel(SQLModel, table=True):
    """Modelo ORM do vínculo de item de estoque em uma Ordem de Serviço."""

    __tablename__ = "ordem_servico_item"
    __table_args__ = (
        sa.CheckConstraint("quantidade > 0", name="ck_os_item_quantidade_positiva"),
        sa.CheckConstraint("valor_unitario >= 0", name="ck_os_item_valor_nao_negativo"),
        sa.CheckConstraint(
            "status IN ('A_RECEBER', 'RESERVADO', 'EM_USO', 'CONSUMIDO', 'CANCELADO')",
            name="ck_os_item_status_operacional",
        ),
    )

    id: UUID = Field(primary_key=True)
    ordem_servico_id: UUID = Field(
        sa_column=sa.Column(sa.Uuid, sa.ForeignKey("ordem_servico.id"), nullable=False)
    )
    item_estoque_id: UUID = Field(
        sa_column=sa.Column(sa.Uuid, nullable=False, index=True)
    )
    nome_item: str = Field(sa_column=sa.Column(sa.String(100), nullable=False))
    tipo_item: str = Field(sa_column=sa.Column(sa.String(10), nullable=False))
    quantidade: int = Field(sa_column=sa.Column(sa.Integer, nullable=False))
    valor_unitario: Decimal = Field(
        sa_column=sa.Column(sa.Numeric(10, 2), nullable=False)
    )
    status: str = Field(sa_column=sa.Column(sa.String(20), nullable=False))
