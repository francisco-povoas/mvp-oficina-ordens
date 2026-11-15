"""Bootstrap idempotente dos perfis e do primeiro administrador."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select

from app.config import get_settings
from app.modules.iam.domain.entities.perfil import PerfilTipos
from app.modules.iam.infrastructure.db.models.perfil import PerfilModel
from app.modules.iam.infrastructure.db.models.usuario import UsuarioModel
from app.modules.iam.infrastructure.db.models.usuario_perfil import UsuarioPerfilModel
from app.shared.infra.db import DBSession, dispose_engine, get_session_factory
from app.shared.infra.security.crypto import Hasher
from app.shared.ports.hasher import HasherProtocol
from app.shared.value_objects.email import Email
from app.shared.value_objects.id import ID
from app.shared.value_objects.password import Password

_BOOTSTRAP_LOCK_ID = 4_321_004
_PROFILE_SEEDS = {
    PerfilTipos.ADMINISTRADOR.value: "Administrador do sistema",
    PerfilTipos.ATENDENTE.value: "Atendente",
    PerfilTipos.MECANICO.value: "Mecânico",
}


class IAMBootstrapError(RuntimeError):
    """Indica uma configuração ou estado incompatível com um bootstrap seguro."""


@dataclass(frozen=True)
class IAMBootstrapConfig:
    admin_nome: str
    admin_email: str
    admin_password: str


@dataclass(frozen=True)
class IAMBootstrapResult:
    profiles_created: tuple[str, ...]
    admin_created: bool
    admin_email: str


async def bootstrap_iam(
    session_factory: async_sessionmaker[DBSession],
    config: IAMBootstrapConfig,
    hasher: HasherProtocol | None = None,
) -> IAMBootstrapResult:
    """Garante os perfis e um administrador sem alterar registros existentes."""

    password_hasher = hasher or Hasher()
    created_profiles: list[str] = []

    async with session_factory() as session, session.begin():
        # Serializa inicializações concorrentes de réplicas da API no PostgreSQL.
        await session.exec(select(func.pg_advisory_xact_lock(_BOOTSTRAP_LOCK_ID)))

        result = await session.exec(select(PerfilModel))
        profiles = {profile.nome: profile for profile in result.all()}
        now = datetime.now(UTC)

        for name, description in _PROFILE_SEEDS.items():
            if name in profiles:
                continue
            profile = PerfilModel(
                id=ID.generate().value,
                nome=name,
                descricao=description,
                criado_em=now,
                atualizado_em=now,
                ativo=True,
            )
            session.add(profile)
            profiles[name] = profile
            created_profiles.append(name)

        await session.flush()
        admin_profile = profiles[PerfilTipos.ADMINISTRADOR.value]
        if not admin_profile.ativo:
            raise IAMBootstrapError(
                "O perfil Administrador existente está inativo; o bootstrap não o altera."
            )

        result = await session.exec(
            select(UsuarioModel)
            .join(UsuarioPerfilModel, UsuarioPerfilModel.usuario_id == UsuarioModel.id)
            .where(UsuarioPerfilModel.perfil_id == admin_profile.id)
            .where(UsuarioModel.ativo.is_(True))
        )
        existing_admin = result.first()
        if existing_admin is not None:
            return IAMBootstrapResult(
                profiles_created=tuple(created_profiles),
                admin_created=False,
                admin_email=existing_admin.email,
            )

        admin_name = config.admin_nome.strip()
        admin_email = config.admin_email.strip()
        if not admin_name or not admin_email or not config.admin_password:
            raise IAMBootstrapError(
                "ADMIN_NOME, ADMIN_EMAIL e ADMIN_PASSWORD devem ser definidos "
                "para criar o primeiro administrador."
            )

        # Reutiliza as mesmas validações e o mesmo algoritmo da autenticação atual.
        validated_email = Email(admin_email).value
        validated_password = Password(config.admin_password).value

        result = await session.exec(
            select(UsuarioModel).where(UsuarioModel.email == validated_email)
        )
        if result.first() is not None:
            raise IAMBootstrapError(
                "Já existe um usuário com ADMIN_EMAIL sem perfil Administrador; "
                "o bootstrap não altera usuários existentes."
            )

        user = UsuarioModel(
            id=ID.generate().value,
            nome=admin_name,
            email=validated_email,
            senha_hash=password_hasher.hash(validated_password),
            criado_em=now,
            atualizado_em=now,
            ativo=True,
        )
        session.add(user)
        await session.flush()
        session.add(
            UsuarioPerfilModel(
                id=ID.generate().value,
                usuario_id=user.id,
                perfil_id=admin_profile.id,
                criado_em=now,
            )
        )

    return IAMBootstrapResult(
        profiles_created=tuple(created_profiles),
        admin_created=True,
        admin_email=validated_email,
    )


async def main() -> None:
    settings = get_settings()
    try:
        result = await bootstrap_iam(
            get_session_factory(),
            IAMBootstrapConfig(
                admin_nome=settings.admin_nome,
                admin_email=settings.admin_email,
                admin_password=settings.admin_password,
            ),
        )
        profile_summary = ", ".join(result.profiles_created) or "nenhum"
        admin_action = "criado" if result.admin_created else "preservado"
        print(
            f"[bootstrap_iam] perfis criados: {profile_summary}; "
            f"administrador {admin_action}: {result.admin_email}"
        )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
