"""Consulta REST autenticada do estado financeiro autoritativo."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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


@dataclass(frozen=True, kw_only=True)
class QuoteFinancialStatus:
    quote_id: UUID
    quote_version: int
    status: str
    total: Decimal


@dataclass(frozen=True, kw_only=True)
class PaymentFinancialStatus:
    payment_id: UUID
    status: str
    amount: Decimal
    confirmed_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class FinancialStatus:
    order_id: UUID
    quote: QuoteFinancialStatus | None
    payment: PaymentFinancialStatus | None
    reconciled_at: datetime


class FinanceStatusPort(Protocol):
    async def obter_status(self, order_id: UUID) -> FinancialStatus | None: ...


class _QuoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: UUID
    quote_version: int = Field(ge=1)
    status: str
    total: Decimal = Field(ge=0)


class _PaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payment_id: UUID
    status: str
    amount: Decimal = Field(gt=0)
    confirmed_at: datetime | None


class _FinancialStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    quote: _QuoteResponse | None
    payment: _PaymentResponse | None
    reconciled_at: datetime


class FinanceHTTPClient(FinanceStatusPort):
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

    async def obter_status(self, order_id: UUID) -> FinancialStatus | None:
        token = await self._token_provider.get_token(
            audience=self._service_name,
            scopes={"finance:status:read"},
            correlation_id=self._correlation_id,
        )
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"/internal/v1/orders/{order_id}/financial-status",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Correlation-ID": self._correlation_id,
                    },
                )
        except httpx.TimeoutException as exc:
            raise DependencyTimeoutError("Timeout ao reconciliar pagamento") from exc
        except httpx.RequestError as exc:
            raise DependencyUnavailableError("Financeiro indisponível") from exc

        validate_correlation(response, self._correlation_id)
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise DependencyAuthenticationError("Token recusado por Financeiro")
        if response.status_code == 403:
            raise DependencyAuthorizationError("Escopo recusado por Financeiro")
        if response.status_code >= 500:
            raise DependencyUnavailableError("Financeiro indisponível")
        if response.status_code != 200:
            raise DependencyContractError("Resposta inesperada do Financeiro")
        try:
            payload = _FinancialStatusResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise DependencyContractError("Estado financeiro incompatível") from exc
        if payload.order_id != order_id:
            raise DependencyContractError("Financeiro respondeu ordem divergente")
        quote = (
            QuoteFinancialStatus(
                quote_id=payload.quote.quote_id,
                quote_version=payload.quote.quote_version,
                status=payload.quote.status,
                total=payload.quote.total,
            )
            if payload.quote
            else None
        )
        payment = (
            PaymentFinancialStatus(
                payment_id=payload.payment.payment_id,
                status=payload.payment.status,
                amount=payload.payment.amount,
                confirmed_at=payload.payment.confirmed_at,
            )
            if payload.payment
            else None
        )
        return FinancialStatus(
            order_id=payload.order_id,
            quote=quote,
            payment=payment,
            reconciled_at=payload.reconciled_at,
        )
