from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_servico import (
    OrdemServicoServico,
)
from app.modules.ordens_servico.presentation.dependencies import (
    get_cliente_repo,
    get_ordem_servico_repo,
)
from app.shared.infra.security.jwt import JWTProvider
from app.shared.value_objects.id import ID


class FakeOrdersRepository:
    def __init__(self, order, services=(), items=()) -> None:
        self.order = order
        self.services = list(services)
        self.items = list(items)

    async def obter_por_id(self, order_id: ID):
        return self.order if self.order and self.order.id == order_id else None

    async def listar_servicos_da_os(self, order_id: ID):
        return self.services

    async def listar_itens_da_os(self, order_id: ID):
        return self.items


class FakeCustomersRepository:
    async def obter_por_id(self, customer_id: ID):
        return SimpleNamespace(
            email=SimpleNamespace(value="cliente@example.com"),
            telefone=SimpleNamespace(value="5511999999999"),
        )


def _order(status: StatusOrdemServico) -> OrdemServico:
    now = datetime.now(UTC)
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=status,
        queixa_inicial="Ruído",
        diagnostico="Rolamento",
        criado_em=now,
        atualizado_em=now,
        iniciado_diagnostico_em=now,
        diagnostico_concluido_em=now,
        version=4,
    )


def _snapshots(order: OrdemServico):
    service = OrdemServicoServico(
        id=ID.generate(),
        ordem_servico_id=order.id.value,
        servico_id=uuid4(),
        nome_servico="Revisão",
        descricao_servico="Revisão completa",
        valor_unitario=Decimal("100.00"),
        tempo_estimado_minutos=60,
        tempo_executado_minutos=None,
        observacao=None,
        cancelado=False,
    )
    item = OrdemServicoItem(
        id=ID.generate(),
        ordem_servico_id=order.id.value,
        item_estoque_id=uuid4(),
        nome_item="Filtro",
        tipo_item="PECA",
        quantidade=2,
        valor_unitario=Decimal("25.00"),
        status=StatusItemNaOS.EM_USO,
    )
    return service, item


def _settings() -> Settings:
    return Settings(environment="test", jwt_secret_key="segredo-de-teste")


def _token(settings: Settings, scope: str) -> str:
    return JWTProvider(
        settings.jwt_secret_key,
        settings.jwt_access_token_expire_minutes,
        settings.jwt_algorithm,
        settings.jwt_issuer,
    ).create_service_token(
        client_id="mvp-oficina-financeiro",
        audience=settings.service_name,
        scopes={scope},
        expire_minutes=5,
    )["access_token"]


def _app(settings: Settings, repository: FakeOrdersRepository):
    app = create_app(settings)
    app.dependency_overrides[get_ordem_servico_repo] = lambda: repository
    app.dependency_overrides[get_cliente_repo] = FakeCustomersRepository
    return app


def test_quote_context_returns_only_authoritative_active_snapshots() -> None:
    settings = _settings()
    order = _order(StatusOrdemServico.DIAGNOSTICO_CONCLUIDO)
    service, item = _snapshots(order)
    app = _app(settings, FakeOrdersRepository(order, [service], [item]))
    correlation_id = str(uuid4())

    with TestClient(app) as client:
        response = client.get(
            f"/internal/v1/orders/{order.id}/quote-context",
            headers={
                "Authorization": f"Bearer {_token(settings, 'orders:quote-context:read')}",
                "X-Correlation-ID": correlation_id,
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json()["order_version"] == 4
    assert response.json()["services"][0]["unit_price"] == "100.00"
    assert response.json()["items"][0]["quantity"] == 2
    assert response.json()["customer_contact"]["email"] == "cliente@example.com"


def test_payment_context_validates_finalized_state_and_snapshot_total() -> None:
    settings = _settings()
    order = _order(StatusOrdemServico.FINALIZADA)
    service, item = _snapshots(order)
    app = _app(settings, FakeOrdersRepository(order, [service], [item]))
    quote_id = uuid4()
    headers = {
        "Authorization": f"Bearer {_token(settings, 'orders:payment-context:read')}",
        "X-Correlation-ID": str(uuid4()),
    }

    with TestClient(app) as client:
        success = client.get(
            f"/internal/v1/orders/{order.id}/payment-context",
            params={
                "approved_quote_id": str(quote_id),
                "approved_quote_version": 3,
                "approved_total": "150.00",
            },
            headers=headers,
        )
        conflict = client.get(
            f"/internal/v1/orders/{order.id}/payment-context",
            params={
                "approved_quote_id": str(quote_id),
                "approved_quote_version": 3,
                "approved_total": "149.99",
            },
            headers=headers,
        )

    assert success.status_code == 200
    assert success.json()["approved_total"] == "150.00"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "ORDER_CONTEXT_CONFLICT"
