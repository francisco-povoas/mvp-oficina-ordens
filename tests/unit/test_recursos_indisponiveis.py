"""O boundary de Recursos permanece fail-closed até a Etapa 2."""

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.integrations.resources import (
    ConsumirReservaCommand,
    LiberarReservaCommand,
    ReceberItemPendenteCommand,
    RecursosIndisponiveisAdapter,
    ReservarItemCommand,
    ResourceDependencyUnavailable,
)
from app.main import create_app
from app.shared.value_objects.id import ID


def _command(command_type):
    return command_type(
        operation_id=uuid4(),
        ordem_servico_id=uuid4(),
        ordem_servico_item_id=uuid4(),
        recurso_id=uuid4(),
        quantidade=2,
    )


async def test_adapter_de_catalogo_falha_explicitamente() -> None:
    with pytest.raises(ResourceDependencyUnavailable, match="operação não concluída"):
        await RecursosIndisponiveisAdapter().obter_por_id(ID.generate())


@pytest.mark.parametrize(
    ("method", "command_type"),
    [
        ("reservar_item", ReservarItemCommand),
        ("liberar_reserva", LiberarReservaCommand),
        ("receber_item_pendente", ReceberItemPendenteCommand),
        ("consumir_reserva", ConsumirReservaCommand),
    ],
)
async def test_operacoes_de_estoque_falham_sem_expor_saldos(method, command_type) -> None:
    adapter = RecursosIndisponiveisAdapter()
    with pytest.raises(ResourceDependencyUnavailable, match="operação não concluída"):
        await getattr(adapter, method)(_command(command_type))


def test_indisponibilidade_controlada_vira_503_e_nao_500() -> None:
    app: FastAPI = create_app(Settings(environment="test"))

    @app.get("/test/resource-unavailable")
    async def unavailable():
        raise ResourceDependencyUnavailable("operação não concluída")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/resource-unavailable")

    assert response.status_code == 503
    assert response.json() == {"detail": "operação não concluída"}


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/ordens-servico/{ordem_servico_id}/itens", "post"),
        (
            "/api/v1/ordens-servico/{ordem_servico_id}/itens/"
            "{ordem_servico_item_id}",
            "delete",
        ),
        (
            "/api/v1/ordens-servico/{ordem_servico_id}/itens/"
            "{ordem_servico_item_id}/confirmar-recebimento",
            "patch",
        ),
        ("/api/v1/ordens-servico/{ordem_servico_id}/entregar", "patch"),
    ],
)
def test_openapi_publica_503_nas_operacoes_distribuidas(path: str, method: str) -> None:
    document = create_app(Settings(environment="test")).openapi()
    assert "503" in document["paths"][path][method]["responses"]
