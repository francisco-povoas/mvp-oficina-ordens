from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class UsuarioModel(SQLModel, table=True):
    """Modelo ORM da tabela de usuários."""

    __tablename__ = "usuario"

    id            : UUID = Field(primary_key=True)
    nome          : str
    email         : str = Field(unique=True)
    senha_hash    : str
    criado_em: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    atualizado_em: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    ativo: bool = Field(default=True, nullable=False)
