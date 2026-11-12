import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from alembic import context
from app.config import get_settings
from app.modules.clientes.infrastructure.db.models.cliente import ClienteModel
from app.modules.iam.infrastructure.db.models.perfil import PerfilModel
from app.modules.iam.infrastructure.db.models.usuario import UsuarioModel
from app.modules.iam.infrastructure.db.models.usuario_perfil import UsuarioPerfilModel
from app.modules.ordens_servico.infrastructure.db.models import (
    DistributedOperationModel,
    OrdemServicoItemModel,
    OrdemServicoModel,
    OrdemServicoServicoModel,
    ReservaItemSagaModel,
)
from app.modules.veiculos.infrastructure.db.models.veiculo import VeiculoModel
from app.shared.infra.db.outbox_model import OutboxMessageModel

_MODELS = (
    PerfilModel,
    UsuarioModel,
    UsuarioPerfilModel,
    ClienteModel,
    VeiculoModel,
    OrdemServicoModel,
    OrdemServicoServicoModel,
    OrdemServicoItemModel,
    DistributedOperationModel,
    ReservaItemSagaModel,
    OutboxMessageModel,
)
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
settings = get_settings()
if settings.database_url:
    config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
