from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.internal.router import router as internal_router
from app.api.v1 import router as v1_router
from app.config import Settings, get_settings
from app.integrations.resources import ResourceDependencyUnavailable
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoConflitoDeVersaoError,
)


async def _resource_unavailable_handler(
    request: Request, exc: ResourceDependencyUnavailable
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


async def _version_conflict_handler(
    request: Request, exc: OrdemServicoConflitoDeVersaoError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="MVP Oficina — Ordens",
        version=resolved_settings.service_version,
    )
    app.state.settings = resolved_settings
    app.add_exception_handler(ResourceDependencyUnavailable, _resource_unavailable_handler)
    app.add_exception_handler(OrdemServicoConflitoDeVersaoError, _version_conflict_handler)
    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(internal_router)
    return app
