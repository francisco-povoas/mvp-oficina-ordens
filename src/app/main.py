from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.internal.router import router as internal_router
from app.api.v1 import router as v1_router
from app.config import Settings, get_settings
from app.http_contract import install_http_contract, problem_response
from app.integrations.http import (
    AsyncServiceTokenProvider,
    DependencyAuthenticationError,
    DependencyAuthorizationError,
    DependencyContractError,
    DependencyTimeoutError,
    DependencyUnavailableError,
    ServiceClientConfig,
)
from app.integrations.resources import ResourceDependencyUnavailable
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoConflitoDeVersaoError,
)


async def _resource_unavailable_handler(
    request: Request, exc: ResourceDependencyUnavailable
) -> JSONResponse:
    return problem_response(
        request,
        status_code=503,
        code="RESOURCE_DEPENDENCY_UNAVAILABLE",
        message="Dependência de Recursos indisponível; operação não concluída",
    )


async def _version_conflict_handler(
    request: Request, exc: OrdemServicoConflitoDeVersaoError
) -> JSONResponse:
    return problem_response(
        request,
        status_code=409,
        code="ORDER_VERSION_CONFLICT",
        message="A ordem de serviço foi alterada por outra operação",
    )


def _dependency_handler(status_code: int, code: str, message: str):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return problem_response(
            request,
            status_code=status_code,
            code=code,
            message=message,
        )

    return handler


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title="MVP Oficina — Ordens",
        version=resolved_settings.service_version,
    )
    app.state.settings = resolved_settings
    app.state.service_token_provider = AsyncServiceTokenProvider(
        ServiceClientConfig(
            token_url=resolved_settings.internal_auth_url,
            client_id=resolved_settings.service_client_id,
            client_secret=resolved_settings.service_client_secret,
            connect_timeout_seconds=resolved_settings.internal_connect_timeout_seconds,
            read_timeout_seconds=resolved_settings.internal_read_timeout_seconds,
        )
    )
    install_http_contract(app)
    app.add_exception_handler(ResourceDependencyUnavailable, _resource_unavailable_handler)
    app.add_exception_handler(OrdemServicoConflitoDeVersaoError, _version_conflict_handler)
    app.add_exception_handler(
        DependencyAuthenticationError,
        _dependency_handler(502, "DEPENDENCY_AUTHENTICATION_FAILED", "Autenticação interna falhou"),
    )
    app.add_exception_handler(
        DependencyAuthorizationError,
        _dependency_handler(502, "DEPENDENCY_AUTHORIZATION_FAILED", "Autorização interna falhou"),
    )
    app.add_exception_handler(
        DependencyContractError,
        _dependency_handler(502, "DEPENDENCY_CONTRACT_INVALID", "Resposta interna incompatível"),
    )
    app.add_exception_handler(
        DependencyTimeoutError,
        _dependency_handler(504, "DEPENDENCY_TIMEOUT", "Dependência interna excedeu o timeout"),
    )
    app.add_exception_handler(
        DependencyUnavailableError,
        _dependency_handler(503, "DEPENDENCY_UNAVAILABLE", "Dependência interna indisponível"),
    )
    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(internal_router)
    return app
