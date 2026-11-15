"""Bootstrap real de IAM e autenticação HTTP contra PostgreSQL isolado."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.bootstrap_iam import IAMBootstrapConfig, bootstrap_iam
from app.config import Settings
from app.main import create_app
from app.modules.iam.infrastructure.db.models.perfil import PerfilModel
from app.modules.iam.infrastructure.db.models.usuario import UsuarioModel
from app.modules.iam.infrastructure.db.models.usuario_perfil import UsuarioPerfilModel
from app.shared.infra.db import get_db_session
from app.shared.infra.security.crypto import Hasher
from app.shared.infra.security.jwt import JWTProvider

ADMIN_EMAIL = "admin.bootstrap@example.com"
ADMIN_PASSWORD = "SenhaBootstrap-2026!"


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _config() -> IAMBootstrapConfig:
    return IAMBootstrapConfig(
        admin_nome="Administrador Bootstrap",
        admin_email=ADMIN_EMAIL,
        admin_password=ADMIN_PASSWORD,
    )


async def test_bootstrap_em_banco_vazio_cria_perfis_e_administrador(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)

    result = await bootstrap_iam(factory, _config())

    async with factory() as session:
        profiles = (await session.exec(select(PerfilModel))).all()
        admin = (
            await session.exec(select(UsuarioModel).where(UsuarioModel.email == ADMIN_EMAIL))
        ).one()
        links = (
            await session.exec(
                select(PerfilModel.nome)
                .join(UsuarioPerfilModel, UsuarioPerfilModel.perfil_id == PerfilModel.id)
                .where(UsuarioPerfilModel.usuario_id == admin.id)
            )
        ).all()

    assert set(result.profiles_created) == {"Administrador", "Atendente", "Mecanico"}
    assert result.admin_created is True
    assert {profile.nome for profile in profiles} == {"Administrador", "Atendente", "Mecanico"}
    assert links == ["Administrador"]
    assert Hasher().verify(ADMIN_PASSWORD, admin.senha_hash)


async def test_bootstrap_repetido_nao_duplica_nem_altera_senha(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    await bootstrap_iam(factory, _config())

    async with factory() as session:
        original_hash = (
            await session.exec(select(UsuarioModel).where(UsuarioModel.email == ADMIN_EMAIL))
        ).one().senha_hash

    result = await bootstrap_iam(
        factory,
        IAMBootstrapConfig(
            admin_nome="Nome que não deve substituir",
            admin_email="outro-admin@example.com",
            admin_password="OutraSenha-2026!",
        ),
    )

    async with factory() as session:
        profile_count = await session.scalar(select(func.count()).select_from(PerfilModel))
        user_count = await session.scalar(select(func.count()).select_from(UsuarioModel))
        link_count = await session.scalar(select(func.count()).select_from(UsuarioPerfilModel))
        admin = (
            await session.exec(select(UsuarioModel).where(UsuarioModel.email == ADMIN_EMAIL))
        ).one()

    assert result.admin_created is False
    assert result.profiles_created == ()
    assert (profile_count, user_count, link_count) == (3, 1, 1)
    assert admin.nome == "Administrador Bootstrap"
    assert admin.senha_hash == original_hash


async def test_bootstrap_preserva_usuario_existente(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    now = datetime.now(UTC)
    existing_id = uuid4()
    existing_hash = Hasher().hash("SenhaExistente-2026!")
    async with factory() as session, session.begin():
        session.add(
            UsuarioModel(
                id=existing_id,
                nome="Usuário Existente",
                email="existente@example.com",
                senha_hash=existing_hash,
                criado_em=now,
                atualizado_em=now,
                ativo=True,
            )
        )

    await bootstrap_iam(factory, _config())

    async with factory() as session:
        existing = await session.get(UsuarioModel, existing_id)
        users = (await session.exec(select(UsuarioModel))).all()

    assert existing is not None
    assert existing.nome == "Usuário Existente"
    assert existing.senha_hash == existing_hash
    assert {user.email for user in users} == {"existente@example.com", ADMIN_EMAIL}


async def test_login_jwt_e_autorizacao_dos_tres_perfis(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    await bootstrap_iam(factory, _config())
    settings = Settings(
        database_url="postgresql+asyncpg://unused-by-overridden-dependency",
        jwt_secret_key="bootstrap-test-secret",
    )
    app = create_app(settings)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        wrong_password = await client.post(
            "/api/v1/auth/token",
            data={"username": ADMIN_EMAIL, "password": "SenhaIncorreta-2026!"},
        )
        admin_login = await client.post(
            "/api/v1/auth/token",
            data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        admin_token = admin_login.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        for name, email, profile in (
            ("Mecânico Validação", "mecanico.bootstrap@example.com", "Mecanico"),
            ("Atendente Validação", "atendente.bootstrap@example.com", "Atendente"),
        ):
            response = await client.post(
                "/api/v1/usuarios",
                headers=admin_headers,
                json={
                    "nome": name,
                    "email": email,
                    "senha": "SenhaPerfil-2026!",
                    "perfis": [profile],
                },
            )
            assert response.status_code == 201

        mechanic_login = await client.post(
            "/api/v1/auth/token",
            data={
                "username": "mecanico.bootstrap@example.com",
                "password": "SenhaPerfil-2026!",
            },
        )
        attendant_login = await client.post(
            "/api/v1/auth/token",
            data={
                "username": "atendente.bootstrap@example.com",
                "password": "SenhaPerfil-2026!",
            },
        )
        mechanic_headers = {
            "Authorization": f"Bearer {mechanic_login.json()['access_token']}"
        }
        attendant_headers = {
            "Authorization": f"Bearer {attendant_login.json()['access_token']}"
        }

        assert (await client.get("/api/v1/usuarios", headers=admin_headers)).status_code == 200
        assert (await client.get("/api/v1/clientes", headers=attendant_headers)).status_code == 200
        assert (await client.get("/api/v1/clientes", headers=mechanic_headers)).status_code == 403
        assert (
            await client.patch(
                f"/api/v1/ordens-servico/{uuid4()}/iniciar-diagnostico",
                headers=mechanic_headers,
            )
        ).status_code == 404
        assert (
            await client.patch(
                f"/api/v1/ordens-servico/{uuid4()}/iniciar-diagnostico",
                headers=attendant_headers,
            )
        ).status_code == 403

    assert wrong_password.status_code == 401
    assert admin_login.status_code == 200
    assert mechanic_login.status_code == 200
    assert attendant_login.status_code == 200
    payload = JWTProvider(
        settings.jwt_secret_key,
        settings.jwt_access_token_expire_minutes,
        settings.jwt_algorithm,
        settings.jwt_issuer,
    ).decode(admin_token, audience=settings.jwt_user_audience)
    assert payload["perfis"] == ["Administrador"]
    assert payload["token_use"] == "user"
