from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.shared.value_objects.id import ID


class ResourceDependencyUnavailable(RuntimeError):
    """O adapter distribuído de Recursos ainda não está disponível nesta etapa."""


class ItemEstoqueNaoEncontradoError(LookupError):
    pass


class ServicoNaoEncontradoError(LookupError):
    pass


class ItemType(StrEnum):
    PECA = "PECA"
    INSUMO = "INSUMO"


@dataclass(frozen=True, kw_only=True)
class ItemEstoqueSnapshot:
    id: ID
    tipo: ItemType
    nome: str
    descricao: str | None
    codigo: str | None
    quantidade_disponivel: int
    quantidade_reservada: int
    quantidade_minima: int
    valor_unitario: Decimal
    ativo: bool
    criado_em: datetime
    atualizado_em: datetime


@dataclass(frozen=True, kw_only=True)
class ServicoSnapshot:
    id: ID
    nome: str
    descricao: str | None
    valor_base: Decimal
    tempo_medio_minutos: int
    ativo: bool
    criado_em: datetime
    atualizado_em: datetime


class ItemEstoquePort(Protocol):
    async def obter_por_id_com_lock(self, item_id: ID) -> ItemEstoqueSnapshot | None: ...

    async def atualizar(self, item: ItemEstoqueSnapshot) -> None: ...


class ServicoCatalogoPort(Protocol):
    async def obter_por_id(self, servico_id: ID) -> ServicoSnapshot | None: ...


class RecursosIndisponiveisAdapter(ItemEstoquePort, ServicoCatalogoPort):
    """Fail-closed até que os adapters REST/mensageria sejam criados na Etapa 2."""

    async def obter_por_id_com_lock(self, item_id: ID) -> ItemEstoqueSnapshot | None:
        raise ResourceDependencyUnavailable("Integração com estoque planejada para a Etapa 2")

    async def atualizar(self, item: ItemEstoqueSnapshot) -> None:
        raise ResourceDependencyUnavailable("Integração com estoque planejada para a Etapa 2")

    async def obter_por_id(self, servico_id: ID) -> ServicoSnapshot | None:
        raise ResourceDependencyUnavailable("Integração com catálogo planejada para a Etapa 2")
