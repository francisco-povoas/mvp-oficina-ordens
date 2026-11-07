from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.security.service_credentials import (
    InvalidServiceClient,
    InvalidServiceGrant,
    authenticate_service_client,
)
from app.shared.infra.api.dependencies.auth import TokenProvider

router = APIRouter()


class ServiceTokenRequest(BaseModel):
    grant_type: Literal["client_credentials"] = "client_credentials"
    client_id: str = Field(min_length=1, max_length=128)
    client_secret: str = Field(min_length=1, max_length=256)
    audience: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1)


class ServiceTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int
    scope: str


@router.post(
    "/auth/token",
    response_model=ServiceTokenResponse,
    summary="Emite token interno HS256 de curta duração",
)
async def issue_service_token(
    body: ServiceTokenRequest,
    request: Request,
    token_provider: TokenProvider,
) -> ServiceTokenResponse:
    settings = request.app.state.settings
    requested_scopes = {scope for scope in body.scope.split() if scope}
    try:
        grant = authenticate_service_client(
            settings,
            client_id=body.client_id,
            client_secret=body.client_secret,
            audience=body.audience,
            requested_scopes=requested_scopes,
        )
    except InvalidServiceClient as exc:
        raise HTTPException(
            status_code=401,
            detail="Credenciais de serviço inválidas",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InvalidServiceGrant as exc:
        raise HTTPException(
            status_code=403,
            detail="Audiência ou escopos não autorizados",
        ) from exc

    token = token_provider.create_service_token(
        client_id=grant.client_id,
        audience=grant.audience,
        scopes=set(grant.scopes),
        expire_minutes=settings.jwt_internal_token_expire_minutes,
    )
    return ServiceTokenResponse(
        access_token=token["access_token"],
        token_type="bearer",
        expires_in=token["expires_in"],
        scope=" ".join(sorted(grant.scopes)),
    )

