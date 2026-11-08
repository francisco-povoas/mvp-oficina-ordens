import json
from uuid import uuid4

import httpx
import pytest

from app.integrations.http import (
    AsyncServiceTokenProvider,
    DependencyAuthenticationError,
    DependencyContractError,
    DependencyTimeoutError,
    ServiceClientConfig,
)
from app.integrations.resources import RecursosHTTPAdapter
from app.shared.value_objects.id import ID


def _config() -> ServiceClientConfig:
    return ServiceClientConfig(
        token_url="http://ordens/internal/v1/auth/token",
        client_id="mvp-oficina-ordens",
        client_secret="credencial-externa",
        connect_timeout_seconds=0.2,
        read_timeout_seconds=0.3,
    )


@pytest.mark.asyncio
async def test_token_is_reused_and_safely_renewed(monkeypatch) -> None:
    correlation_id = str(uuid4())
    service_id = uuid4()
    clock = [0.0]
    token_requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/token"):
            token_requests.append(json.loads(request.content))
            token = f"token-{len(token_requests)}"
            return httpx.Response(
                200,
                headers={"X-Correlation-ID": correlation_id},
                json={
                    "access_token": token,
                    "token_type": "bearer",
                    "expires_in": 20,
                    "scope": "resources:catalog:read",
                },
            )
        assert request.headers["X-Correlation-ID"] == correlation_id
        assert request.headers["Authorization"].startswith("Bearer token-")
        return httpx.Response(
            200,
            headers={"X-Correlation-ID": correlation_id},
            json={
                "service_id": str(service_id),
                "name": "Revisão",
                "description": "Revisão completa",
                "price": "150.00",
                "estimated_minutes": 60,
                "active": True,
                "version": 1,
            },
        )

    monkeypatch.setattr("app.integrations.http.time.monotonic", lambda: clock[0])
    transport = httpx.MockTransport(handler)
    provider = AsyncServiceTokenProvider(_config(), transport=transport)
    adapter = RecursosHTTPAdapter(
        base_url="http://recursos",
        service_name="mvp-oficina-recursos",
        token_provider=provider,
        correlation_id=correlation_id,
        timeout=_config().timeout,
        transport=transport,
    )

    first = await adapter.obter_por_id(ID(service_id))
    clock[0] = 1
    second = await adapter.obter_por_id(ID(service_id))
    clock[0] = 6
    third = await adapter.obter_por_id(ID(service_id))

    assert first == second == third
    assert len(token_requests) == 2
    assert token_requests[0]["audience"] == "mvp-oficina-recursos"
    assert token_requests[0]["scope"] == "resources:catalog:read"


@pytest.mark.asyncio
async def test_catalog_distinguishes_not_found_auth_timeout_and_contract() -> None:
    correlation_id = str(uuid4())
    service_id = uuid4()

    def adapter_for(status: int | str) -> RecursosHTTPAdapter:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/token"):
                return httpx.Response(
                    200,
                    headers={"X-Correlation-ID": correlation_id},
                    json={
                        "access_token": "token",
                        "token_type": "bearer",
                        "expires_in": 300,
                        "scope": "resources:catalog:read",
                    },
                )
            if status == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            if status == "invalid":
                return httpx.Response(
                    200,
                    headers={"X-Correlation-ID": correlation_id},
                    json={"service_id": str(service_id)},
                )
            return httpx.Response(
                int(status),
                headers={"X-Correlation-ID": correlation_id},
                json={"code": "ERROR"},
            )

        transport = httpx.MockTransport(handler)
        return RecursosHTTPAdapter(
            base_url="http://recursos",
            service_name="mvp-oficina-recursos",
            token_provider=AsyncServiceTokenProvider(_config(), transport=transport),
            correlation_id=correlation_id,
            timeout=_config().timeout,
            transport=transport,
        )

    assert await adapter_for(404).obter_por_id(ID(service_id)) is None
    with pytest.raises(DependencyAuthenticationError):
        await adapter_for(401).obter_por_id(ID(service_id))
    with pytest.raises(DependencyTimeoutError):
        await adapter_for("timeout").obter_por_id(ID(service_id))
    with pytest.raises(DependencyContractError):
        await adapter_for("invalid").obter_por_id(ID(service_id))
