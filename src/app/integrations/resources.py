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

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.integrations.http import (
    AsyncServiceTokenProvider,
    DependencyAuthenticationError,
    DependencyAuthorizationError,
    DependencyContractError,
    DependencyTimeoutError,
    DependencyUnavailableError,
    validate_correlation,
)
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
    criado_em: datetime | None = None
    atualizado_em: datetime | None = None


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


class _ServiceSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    name: str
    description: str | None
    price: Decimal = Field(ge=0)
    estimated_minutes: int = Field(gt=0)
    active: bool
    version: int = Field(ge=1)


class RecursosHTTPAdapter(RecursosIndisponiveisAdapter):
    """REST somente para catálogo; operações de estoque continuam fail-closed."""

    def __init__(
        self,
        *,
        base_url: str,
        service_name: str,
        token_provider: AsyncServiceTokenProvider,
        correlation_id: str,
        timeout: httpx.Timeout,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_name = service_name
        self._token_provider = token_provider
        self._correlation_id = correlation_id
        self._timeout = timeout
        self._transport = transport

    async def obter_por_id(self, servico_id: ID) -> ServicoSnapshot | None:
        token = await self._token_provider.get_token(
            audience=self._service_name,
            scopes={"resources:catalog:read"},
            correlation_id=self._correlation_id,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/internal/v1/catalog/services/{servico_id.value}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Correlation-ID": self._correlation_id,
                    },
                )
        except httpx.TimeoutException as exc:
            raise DependencyTimeoutError("Timeout ao consultar catálogo") from exc
        except httpx.RequestError as exc:
            raise DependencyUnavailableError("Catálogo de Recursos indisponível") from exc

        validate_correlation(response, self._correlation_id)
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise DependencyAuthenticationError("Token recusado por Recursos")
        if response.status_code == 403:
            raise DependencyAuthorizationError("Escopo recusado por Recursos")
        if response.status_code >= 500:
            raise DependencyUnavailableError("Catálogo de Recursos indisponível")
        if response.status_code != 200:
            raise DependencyContractError("Resposta inesperada do catálogo")
        try:
            payload = _ServiceSnapshotResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise DependencyContractError("Snapshot de serviço incompatível") from exc
        if payload.service_id != servico_id.value:
            raise DependencyContractError("Catálogo respondeu identificador divergente")
        return ServicoSnapshot(
            id=ID(payload.service_id),
            nome=payload.name,
            descricao=payload.description,
            valor_base=payload.price,
            tempo_medio_minutos=payload.estimated_minutes,
            ativo=payload.active,
        )
