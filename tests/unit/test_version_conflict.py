"""Conflitos otimistas são respostas controladas, não erro interno."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoConflitoDeVersaoError,
)


def test_conflito_de_versao_vira_http_409() -> None:
    app: FastAPI = create_app(Settings(environment="test"))

    @app.get("/test/version-conflict")
    async def conflict():
        raise OrdemServicoConflitoDeVersaoError("versão concorrente")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test/version-conflict")

    assert response.status_code == 409
    assert response.json() == {"detail": "versão concorrente"}
