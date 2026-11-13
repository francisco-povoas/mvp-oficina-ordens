from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.integrations.finance import FinanceHTTPClient
from app.integrations.http import (
    AsyncServiceTokenProvider,
    DependencyAuthenticationError,
    DependencyAuthorizationError,
    DependencyContractError,
    DependencyTimeoutError,
    DependencyUnavailableError,
    ServiceClientConfig,
)


def _config() -> ServiceClientConfig:
    return ServiceClientConfig(
        token_url="http://ordens/internal/v1/auth/token",
        client_id="mvp-oficina-ordens",
        client_secret="credencial-externa",
        connect_timeout_seconds=0.2,
        read_timeout_seconds=0.3,
    )


def _client(status, *, order_id=None):
    correlation_id = str(uuid4())
    expected_order = order_id or uuid4()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/token"):
            return httpx.Response(
                200,
                headers={"X-Correlation-ID": correlation_id},
                json={
                    "access_token": "token",
                    "token_type": "bearer",
                    "expires_in": 300,
                    "scope": "finance:status:read",
                },
            )
        if status == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if status == "invalid":
            return httpx.Response(
                200,
                headers={"X-Correlation-ID": correlation_id},
                json={"order_id": str(expected_order)},
            )
        if status == 200:
            now = datetime.now(UTC).isoformat()
            quote_id = uuid4()
            return httpx.Response(
                200,
                headers={"X-Correlation-ID": correlation_id},
                json={
                    "order_id": str(expected_order),
                    "quote": {
                        "quote_id": str(quote_id),
                        "quote_version": 3,
                        "status": "APROVADO",
                        "total": "150.00",
                        "communicated_at": now,
                        "decided_at": now,
                        "rejection_reason": None,
                    },
                    "payment": {
                        "payment_id": str(uuid4()),
                        "quote_id": str(quote_id),
                        "quote_version": 3,
                        "attempt_number": 1,
                        "payment_version": 2,
                        "status": "CONFIRMED",
                        "amount": "150.00",
                        "confirmed_at": now,
                    },
                    "reconciled_at": now,
                },
            )
        return httpx.Response(
            status,
            headers={"X-Correlation-ID": correlation_id},
            json={"code": "ERROR"},
        )

    transport = httpx.MockTransport(handler)
    return expected_order, FinanceHTTPClient(
        base_url="http://financeiro",
        service_name="mvp-oficina-financeiro",
        token_provider=AsyncServiceTokenProvider(_config(), transport=transport),
        correlation_id=correlation_id,
        timeout=_config().timeout,
        transport=transport,
    )


async def test_status_financeiro_materializa_identidades_e_versoes() -> None:
    order_id, client = _client(200)
    status = await client.obter_status(order_id)
    assert status.order_id == order_id
    assert status.quote.quote_version == 3
    assert status.payment.quote_id == status.quote.quote_id
    assert status.payment.attempt_number == 1
    assert status.payment.payment_version == 2


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (401, DependencyAuthenticationError),
        (403, DependencyAuthorizationError),
        (500, DependencyUnavailableError),
        ("timeout", DependencyTimeoutError),
        ("invalid", DependencyContractError),
    ],
)
async def test_status_financeiro_classifica_falhas(response, error) -> None:
    order_id, client = _client(response)
    with pytest.raises(error):
        await client.obter_status(order_id)


async def test_status_financeiro_diferencia_ausencia_autoritativa() -> None:
    order_id, client = _client(404)
    assert await client.obter_status(order_id) is None
