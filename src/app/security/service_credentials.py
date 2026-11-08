"""Validação local das credenciais de serviço configuradas por Secret."""

import json
from dataclasses import dataclass

import bcrypt

from app.config import Settings


class InvalidServiceClient(Exception):
    pass


class InvalidServiceGrant(Exception):
    pass


@dataclass(frozen=True)
class ServiceGrant:
    client_id: str
    audience: str
    scopes: frozenset[str]


def authenticate_service_client(
    settings: Settings,
    *,
    client_id: str,
    client_secret: str,
    audience: str,
    requested_scopes: set[str],
) -> ServiceGrant:
    try:
        clients = json.loads(settings.service_clients_json)
        client = clients[client_id]
        secret_hash = client["secret_hash"]
        grants = client["grants"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InvalidServiceClient from exc

    if (
        not isinstance(secret_hash, str)
        or not isinstance(client_secret, str)
        or not client_secret
        or len(client_secret.encode()) > 72
    ):
        raise InvalidServiceClient

    try:
        valid_secret = bcrypt.checkpw(
            client_secret.encode(),
            secret_hash.encode(),
        )
    except ValueError as exc:
        raise InvalidServiceClient from exc
    if not valid_secret:
        raise InvalidServiceClient

    allowed = grants.get(audience) if isinstance(grants, dict) else None
    if (
        not isinstance(allowed, list)
        or not all(isinstance(scope, str) and scope for scope in allowed)
        or not requested_scopes
        or not requested_scopes.issubset(set(allowed))
    ):
        raise InvalidServiceGrant

    return ServiceGrant(
        client_id=client_id,
        audience=audience,
        scopes=frozenset(requested_scopes),
    )

