import json
from uuid import uuid4

import bcrypt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.modules.ordens_servico.presentation.dependencies import get_ordem_servico_repo
from app.shared.infra.api.dependencies.auth import get_usuario_autenticado
from app.shared.infra.security.jwt import InvalidToken, JWTProvider

SECRET = "segredo-hs256-exclusivo-dos-testes"
CLIENT_SECRET = "credencial-interna-de-teste"
CORRELATION_ID = str(uuid4())


class MissingOrderRepository:
    async def obter_por_id(self, order_id):
        return None


def _settings() -> Settings:
    secret_hash = bcrypt.hashpw(CLIENT_SECRET.encode(), bcrypt.gensalt()).decode()
    registry = {
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
    return Settings(
        environment="test",
        jwt_secret_key=SECRET,
        service_clients_json=json.dumps(registry),
    )


def _provider(settings: Settings) -> JWTProvider:
    return JWTProvider(
        settings.jwt_secret_key,
        settings.jwt_access_token_expire_minutes,
        settings.jwt_algorithm,
        settings.jwt_issuer,
    )


def _issue(client: TestClient, **overrides) -> dict:
    body = {
        "grant_type": "client_credentials",
        "client_id": "mvp-oficina-financeiro",
        "client_secret": CLIENT_SECRET,
        "audience": "mvp-oficina-ordens",
        "scope": "orders:quote-context:read",
        **overrides,
    }
    response = client.post(
        "/internal/v1/auth/token",
        json=body,
        headers={"X-Correlation-ID": CORRELATION_ID},
    )
    assert response.status_code == 200
    return response.json()


def test_internal_token_endpoint_emits_short_lived_service_token() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_ordem_servico_repo] = MissingOrderRepository
    with TestClient(app) as client:
        issued = _issue(client)

    claims = _provider(settings).decode(
        issued["access_token"],
        audience="mvp-oficina-ordens",
    )
    assert claims["token_use"] == "service"
    assert claims["sub"] == "service:mvp-oficina-financeiro"
    assert claims["scope"] == "orders:quote-context:read"
    assert claims["iss"] == "mvp-oficina-ordens"
    assert 0 < issued["expires_in"] <= 300


@pytest.mark.parametrize(
    ("override", "status"),
    [
        ({"client_secret": "incorreto"}, 401),
        ({"audience": "mvp-oficina-financeiro"}, 403),
        ({"scope": "finance:status:read"}, 403),
    ],
)
def test_internal_token_endpoint_rejects_invalid_client_or_grant(override, status) -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/internal/v1/auth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": "mvp-oficina-financeiro",
                "client_secret": CLIENT_SECRET,
                "audience": "mvp-oficina-ordens",
                "scope": "orders:quote-context:read",
                **override,
            },
            headers={"X-Correlation-ID": CORRELATION_ID},
        )
    assert response.status_code == status


def test_internal_endpoint_rejects_user_token_and_accepts_exact_service_grant() -> None:
    settings = _settings()
    provider = _provider(settings)
    user = provider.create_user_token(
        subject=str(uuid4()),
        roles=["Administrador"],
        audience=settings.jwt_user_audience,
    )["access_token"]

    app = create_app(settings)
    app.dependency_overrides[get_ordem_servico_repo] = MissingOrderRepository
    with TestClient(app) as client:
        rejected = client.get(
            f"/internal/v1/orders/{uuid4()}/quote-context",
            headers={
                "Authorization": f"Bearer {user}",
                "X-Correlation-ID": CORRELATION_ID,
            },
        )
        service = _issue(client)["access_token"]
        accepted = client.get(
            f"/internal/v1/orders/{uuid4()}/quote-context",
            headers={
                "Authorization": f"Bearer {service}",
                "X-Correlation-ID": CORRELATION_ID,
            },
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 404


def test_internal_endpoint_rejects_wrong_scope_audience_and_expiration() -> None:
    settings = _settings()
    provider = _provider(settings)
    wrong_scope = provider.create_service_token(
        client_id="mvp-oficina-financeiro",
        audience=settings.service_name,
        scopes={"orders:payment-context:read"},
        expire_minutes=5,
    )["access_token"]
    wrong_audience = provider.create_service_token(
        client_id="mvp-oficina-financeiro",
        audience="mvp-oficina-recursos",
        scopes={"orders:quote-context:read"},
        expire_minutes=5,
    )["access_token"]
    expired = provider.create_service_token(
        client_id="mvp-oficina-financeiro",
        audience=settings.service_name,
        scopes={"orders:quote-context:read"},
        expire_minutes=-1,
    )["access_token"]

    with TestClient(create_app(settings)) as client:
        path = f"/internal/v1/orders/{uuid4()}/quote-context"
        headers = {"X-Correlation-ID": CORRELATION_ID}
        assert client.get(
            path,
            headers={**headers, "Authorization": f"Bearer {wrong_scope}"},
        ).status_code == 403
        assert client.get(
            path,
            headers={**headers, "Authorization": f"Bearer {wrong_audience}"},
        ).status_code == 401
        assert client.get(
            path,
            headers={**headers, "Authorization": f"Bearer {expired}"},
        ).status_code == 401


def test_public_user_dependency_rejects_service_token() -> None:
    settings = _settings()
    token = _provider(settings).create_service_token(
        client_id="mvp-oficina-financeiro",
        audience=settings.jwt_user_audience,
        scopes={"orders:quote-context:read"},
        expire_minutes=5,
    )["access_token"]
    app = FastAPI()
    app.state.settings = settings

    @app.get("/public", dependencies=[Depends(get_usuario_autenticado)])
    async def public_probe() -> dict:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get(
            "/public",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


def test_user_validation_requires_token_use_and_audience() -> None:
    settings = _settings()
    provider = _provider(settings)
    missing_type = provider.create_access_token(
        {"sub": str(uuid4()), "perfis": [], "aud": settings.jwt_user_audience}
    )["access_token"]
    wrong_audience = provider.create_user_token(
        subject=str(uuid4()),
        roles=[],
        audience="outro-publico",
    )["access_token"]

    with pytest.raises(InvalidToken):
        provider.get_sub(missing_type, audience=settings.jwt_user_audience)
    with pytest.raises(InvalidToken):
        provider.get_sub(wrong_audience, audience=settings.jwt_user_audience)


def test_provider_refuses_algorithm_other_than_hs256() -> None:
    with pytest.raises(ValueError, match="HS256"):
        JWTProvider(SECRET, 5, "RS256")

