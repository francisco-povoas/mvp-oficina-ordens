import json
from pathlib import Path
from uuid import uuid4

import bcrypt
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from app.config import Settings
from app.main import create_app
from app.modules.ordens_servico.presentation.dependencies import get_ordem_servico_repo
from app.shared.infra.security.jwt import JWTProvider

CONTRACT = json.loads(
    Path("contracts/openapi/internal-v1.openapi.json").read_text(encoding="utf-8")
)
PROBLEM = CONTRACT["components"]["schemas"]["Problem"]
CORRELATION_ID = str(uuid4())
SECRET = "segredo-hs256-exclusivo-dos-testes"
CLIENT_SECRET = "credencial-interna-de-teste"


class MissingOrderRepository:
    async def obter_por_id(self, order_id):
        return None


def _settings() -> Settings:
    secret_hash = bcrypt.hashpw(CLIENT_SECRET.encode(), bcrypt.gensalt()).decode()
    return Settings(
        environment="test",
        jwt_secret_key=SECRET,
        service_clients_json=json.dumps(
            {
                "mvp-oficina-financeiro": {
                    "secret_hash": secret_hash,
                    "grants": {
                        "mvp-oficina-ordens": [
                            "orders:quote-context:read",
                            "orders:payment-context:read",
                        ]
                    },
                }
            }
        ),
    )


def _provider(settings: Settings) -> JWTProvider:
    return JWTProvider(
        settings.jwt_secret_key,
        settings.jwt_access_token_expire_minutes,
        settings.jwt_algorithm,
        settings.jwt_issuer,
    )


def _assert_problem(response, status_code: int, correlation_id: str | None = None) -> None:
    assert response.status_code == status_code
    Draft202012Validator(PROBLEM, format_checker=FormatChecker()).validate(
        response.json()
    )
    assert "detail" not in response.json()
    assert response.headers["X-Correlation-ID"] == response.json()["correlation_id"]
    if correlation_id:
        assert response.json()["correlation_id"] == correlation_id


def test_success_response_echoes_correlation_and_matches_declared_schema() -> None:
    settings = _settings()
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/internal/v1/auth/token",
            headers={"X-Correlation-ID": CORRELATION_ID},
            json={
                "grant_type": "client_credentials",
                "client_id": "mvp-oficina-financeiro",
                "client_secret": CLIENT_SECRET,
                "audience": "mvp-oficina-ordens",
                "scope": "orders:quote-context:read",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == CORRELATION_ID
    Draft202012Validator(
        CONTRACT["components"]["schemas"]["ServiceTokenResponse"]
    ).validate(response.json())


def test_authentication_authorization_and_not_found_errors_match_problem() -> None:
    settings = _settings()
    provider = _provider(settings)
    wrong_scope = provider.create_service_token(
        client_id="mvp-oficina-financeiro",
        audience=settings.service_name,
        scopes={"orders:payment-context:read"},
        expire_minutes=5,
    )["access_token"]
    correct = provider.create_service_token(
        client_id="mvp-oficina-financeiro",
        audience=settings.service_name,
        scopes={"orders:quote-context:read"},
        expire_minutes=5,
    )["access_token"]
    path = f"/internal/v1/orders/{uuid4()}/quote-context"

    app = create_app(settings)
    app.dependency_overrides[get_ordem_servico_repo] = MissingOrderRepository
    with TestClient(app) as client:
        unauthorized = client.get(
            path, headers={"X-Correlation-ID": CORRELATION_ID}
        )
        forbidden = client.get(
            path,
            headers={
                "X-Correlation-ID": CORRELATION_ID,
                "Authorization": f"Bearer {wrong_scope}",
            },
        )
        not_found = client.get(
            path,
            headers={
                "X-Correlation-ID": CORRELATION_ID,
                "Authorization": f"Bearer {correct}",
            },
        )
        missing_correlation = client.get(
            path, headers={"Authorization": f"Bearer {correct}"}
        )

    _assert_problem(unauthorized, 401, CORRELATION_ID)
    _assert_problem(forbidden, 403, CORRELATION_ID)
    _assert_problem(not_found, 404, CORRELATION_ID)
    _assert_problem(missing_correlation, 422)
    assert not_found.json()["code"] == "ORDER_NOT_FOUND"


def test_unexpected_failure_does_not_expose_internal_detail() -> None:
    app = create_app(_settings())

    @app.get("/test/internal-error")
    async def internal_error() -> None:
        raise RuntimeError("segredo-interno-nao-pode-vazar")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/test/internal-error",
            headers={"X-Correlation-ID": CORRELATION_ID},
        )

    _assert_problem(response, 500, CORRELATION_ID)
    assert "segredo-interno" not in response.text
