"""Contratos de aplicação entre Ordens e o serviço de Recursos.

Ordens conhece somente snapshots necessários ao seu histórico e resultados de
operações de negócio. Saldos, locks e decisões de estoque pertencem a Recursos.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.shared.value_objects.id import ID


class ResourceDependencyUnavailable(RuntimeError):
    """A coordenação distribuída com Recursos ainda não está disponível."""


class ItemEstoqueNaoEncontradoError(LookupError):
    pass


class ServicoNaoEncontradoError(LookupError):
    pass


class ItemType(StrEnum):
    PECA = "PECA"
    INSUMO = "INSUMO"


class ReservaItemStatus(StrEnum):
    RESERVADO = "RESERVADO"
    A_RECEBER = "A_RECEBER"


@dataclass(frozen=True, kw_only=True)
class RecursoSnapshot:
    """Snapshot histórico retornado por Recursos, deliberadamente sem saldos."""

    id: ID
    tipo: ItemType
    nome: str
    descricao: str | None
    valor_unitario: Decimal


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


@dataclass(frozen=True, kw_only=True)
class ReservarItemCommand:
    operation_id: UUID
    ordem_servico_id: UUID
    ordem_servico_item_id: UUID
    recurso_id: UUID
    quantidade: int


@dataclass(frozen=True, kw_only=True)
class ReservarItemResult:
    operation_id: UUID
    recurso_id: UUID
    quantidade: int
    status: ReservaItemStatus
    recurso: RecursoSnapshot
    reservation_id: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class LiberarReservaCommand:
    operation_id: UUID
    ordem_servico_id: UUID
    ordem_servico_item_id: UUID
    recurso_id: UUID
    quantidade: int


@dataclass(frozen=True, kw_only=True)
class LiberarReservaResult:
    operation_id: UUID
    recurso_id: UUID
    quantidade: int
    reservation_id: UUID
    liberado_em: datetime


@dataclass(frozen=True, kw_only=True)
class ReceberItemPendenteCommand:
    operation_id: UUID
    ordem_servico_id: UUID
    ordem_servico_item_id: UUID
    recurso_id: UUID
    quantidade: int


@dataclass(frozen=True, kw_only=True)
class ReceberItemPendenteResult:
    operation_id: UUID
    recurso_id: UUID
    quantidade: int
    reservation_id: UUID
    recebido_em: datetime


@dataclass(frozen=True, kw_only=True)
class ConsumirReservaCommand:
    operation_id: UUID
    ordem_servico_id: UUID
    ordem_servico_item_id: UUID
    recurso_id: UUID
    quantidade: int


@dataclass(frozen=True, kw_only=True)
class ConsumirReservaResult:
    operation_id: UUID
    recurso_id: UUID
    quantidade: int
    reservation_id: UUID
    consumido_em: datetime


class RecursosOperacoesPort(Protocol):
    """Operações autoritativas de Recursos; nunca expõe escrita genérica de saldo."""

    async def reservar_item(self, command: ReservarItemCommand) -> ReservarItemResult: ...

    async def liberar_reserva(
        self, command: LiberarReservaCommand
    ) -> LiberarReservaResult: ...

    async def receber_item_pendente(
        self, command: ReceberItemPendenteCommand
    ) -> ReceberItemPendenteResult: ...

    async def consumir_reserva(
        self, command: ConsumirReservaCommand
    ) -> ConsumirReservaResult: ...


class ServicoCatalogoPort(Protocol):
    async def obter_por_id(self, servico_id: ID) -> ServicoSnapshot | None: ...


class RecursosIndisponiveisAdapter(RecursosOperacoesPort, ServicoCatalogoPort):
    """Fail-closed até REST, mensageria e coordenações da Etapa 2 existirem."""

    _MENSAGEM = "Coordenação com Recursos planejada para a Etapa 2; operação não concluída"

    async def reservar_item(self, command: ReservarItemCommand) -> ReservarItemResult:
        raise ResourceDependencyUnavailable(self._MENSAGEM)

    async def liberar_reserva(
        self, command: LiberarReservaCommand
    ) -> LiberarReservaResult:
        raise ResourceDependencyUnavailable(self._MENSAGEM)

    async def receber_item_pendente(
        self, command: ReceberItemPendenteCommand
    ) -> ReceberItemPendenteResult:
        raise ResourceDependencyUnavailable(self._MENSAGEM)

    async def consumir_reserva(
        self, command: ConsumirReservaCommand
    ) -> ConsumirReservaResult:
        raise ResourceDependencyUnavailable(self._MENSAGEM)

    async def obter_por_id(self, servico_id: ID) -> ServicoSnapshot | None:
        raise ResourceDependencyUnavailable(
            "Integração com catálogo planejada para a Etapa 2; operação não concluída"
        )
