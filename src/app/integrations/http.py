"""Infraestrutura HTTP autenticada para chamadas internas da Etapa 2."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class InternalHTTPError(RuntimeError):
    """Falha técnica em uma dependência interna, nunca uma rejeição de negócio."""


class DependencyAuthenticationError(InternalHTTPError):
    pass


class DependencyAuthorizationError(InternalHTTPError):
    pass


class DependencyUnavailableError(InternalHTTPError):
    pass


class DependencyTimeoutError(InternalHTTPError):
    pass


class DependencyContractError(InternalHTTPError):
    pass


class ServiceTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1)
    token_type: str
    expires_in: int = Field(gt=0, le=300)
    scope: str = Field(min_length=1)


@dataclass(frozen=True, kw_only=True)
class ServiceClientConfig:
    token_url: str
    client_id: str
    client_secret: str
    connect_timeout_seconds: float
    read_timeout_seconds: float

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.read_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )


class AsyncServiceTokenProvider:
    """Obtém client_credentials e reutiliza o JWT até a janela de renovação."""

    def __init__(
        self,
        config: ServiceClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._cache: dict[tuple[str, tuple[str, ...]], tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_token(
        self,
        *,
        audience: str,
        scopes: set[str],
        correlation_id: str,
    ) -> str:
        key = (audience, tuple(sorted(scopes)))
        cached = self._cache.get(key)
        if cached is not None and cached[1] > time.monotonic() + 15:
            return cached[0]

        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[1] > time.monotonic() + 15:
                return cached[0]
            token, expires_in = await self._request_token(
                audience=audience,
                scopes=scopes,
                correlation_id=correlation_id,
            )
            self._cache[key] = (token, time.monotonic() + expires_in)
            return token

    async def _request_token(
        self,
        *,
        audience: str,
        scopes: set[str],
        correlation_id: str,
    ) -> tuple[str, int]:
        try:
            async with httpx.AsyncClient(
                timeout=self._config.timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._config.token_url,
                    headers={"X-Correlation-ID": correlation_id},
                    json={
                        "grant_type": "client_credentials",
                        "client_id": self._config.client_id,
                        "client_secret": self._config.client_secret,
                        "audience": audience,
                        "scope": " ".join(sorted(scopes)),
                    },
                )
        except httpx.TimeoutException as exc:
            raise DependencyTimeoutError("Timeout ao obter token de serviço") from exc
        except httpx.RequestError as exc:
            raise DependencyUnavailableError(
                "Emissor de token de serviço indisponível"
            ) from exc

        if response.status_code == 401:
            raise DependencyAuthenticationError("Credencial de serviço rejeitada")
        if response.status_code == 403:
            raise DependencyAuthorizationError("Grant de serviço rejeitado")
        if response.status_code >= 500:
            raise DependencyUnavailableError("Emissor de token de serviço indisponível")
        if response.status_code != 200:
            raise DependencyContractError("Resposta inesperada do emissor de token")
        validate_correlation(response, correlation_id)
        try:
            payload = ServiceTokenResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise DependencyContractError("Token de serviço incompatível") from exc
        if payload.token_type.lower() != "bearer":
            raise DependencyContractError("Tipo de token de serviço incompatível")
        returned_scopes = {scope for scope in payload.scope.split() if scope}
        if not scopes.issubset(returned_scopes):
            raise DependencyAuthorizationError("Token emitido sem os escopos solicitados")
        return payload.access_token, payload.expires_in


def validate_correlation(response: httpx.Response, expected: str) -> None:
    if response.headers.get("X-Correlation-ID") != expected:
        raise DependencyContractError("Resposta interna sem correlação compatível")
