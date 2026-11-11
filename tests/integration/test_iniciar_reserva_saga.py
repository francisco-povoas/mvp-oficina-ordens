"""Início idempotente da Saga e Outbox na mesma transação PostgreSQL."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.integrations.resources import ItemType, RecursoSnapshot
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    AdicionarItemNaOSRequest,
)
from app.modules.ordens_servico.application.use_cases.adicionar_item_na_ordem_servico import (
    AdicionarItemNaOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    EstadoReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ReservaItemSagaIdempotenciaConflitanteError,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_item_model import (
    OrdemServicoItemModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
    OrdemServicoModel,
)
from app.modules.ordens_servico.infrastructure.db.models.reserva_item_saga_model import (
    ReservaItemSagaModel,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.shared.infra.db.outbox_model import OutboxMessageModel
from app.shared.value_objects.id import ID


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _persistir_ordem(session: AsyncSession) -> UUID:
    agora = datetime.now(UTC)
    cliente_id = uuid4()
    veiculo_id = uuid4()
    ordem_id = uuid4()
    await session.exec(
        text(
            "INSERT INTO cliente "
            "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, criado_em, atualizado_em) "
            "VALUES (:id, 'PF', 'Cliente Início Saga', :cpf, '71999999999', true, :agora, :agora)"
        ),
        params={"id": cliente_id, "cpf": str(cliente_id.int)[-11:], "agora": agora},
    )
    await session.exec(
        text(
            "INSERT INTO veiculo "
            "(id, cliente_id, placa, marca, modelo, criado_em, atualizado_em) "
            "VALUES (:id, :cliente_id, :placa, 'Ford', 'Ka', :agora, :agora)"
        ),
        params={
            "id": veiculo_id,
            "cliente_id": cliente_id,
            "placa": f"I{str(veiculo_id).replace('-', '')[:6]}",
            "agora": agora,
        },
    )
    await session.exec(
        text(
            "INSERT INTO ordem_servico "
            "(id, cliente_id, veiculo_id, status, queixa_inicial, criado_em, atualizado_em, version) "
            "VALUES (:id, :cliente_id, :veiculo_id, 'EM_DIAGNOSTICO', 'Ruído', :agora, :agora, 1)"
        ),
        params={
            "id": ordem_id,
            "cliente_id": cliente_id,
            "veiculo_id": veiculo_id,
            "agora": agora,
        },
    )
    await session.commit()
    return ordem_id


@dataclass
class _CatalogoFake:
    snapshot: RecursoSnapshot | None

    async def obter_recurso_por_id(self, recurso_id: ID) -> RecursoSnapshot | None:
        if self.snapshot is not None:
            assert self.snapshot.id == recurso_id
        return self.snapshot


def _snapshot(resource_id: UUID) -> RecursoSnapshot:
    return RecursoSnapshot(
        id=ID(resource_id),
        tipo=ItemType.PECA,
        nome="Filtro de óleo",
        descricao="Snapshot do catálogo",
        valor_unitario=Decimal("45.90"),
    )


async def test_inicio_repeticao_e_outbox_sao_atomicos(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
    resource_id = uuid4()
    dto = AdicionarItemNaOSRequest(item_estoque_id=str(resource_id), quantidade=2)
    correlation_id = uuid4()

    primeira = await AdicionarItemNaOrdemServicoUseCase(
        uow=OrdemServicoUnitOfWork(factory()),
        recursos=_CatalogoFake(_snapshot(resource_id)),
    ).execute(
        str(ordem_id),
        dto,
        idempotency_key="request-atomic-123",
        correlation_id=correlation_id,
    )
    repetida = await AdicionarItemNaOrdemServicoUseCase(
        uow=OrdemServicoUnitOfWork(factory()),
        recursos=_CatalogoFake(_snapshot(resource_id)),
    ).execute(
        str(ordem_id),
        dto,
        idempotency_key="request-atomic-123",
        correlation_id=uuid4(),
    )

    assert primeira == repetida
    assert primeira.estado == EstadoReservaItemSaga.PENDING.value
    async with factory() as session:
        saga = (
            await session.exec(
                select(ReservaItemSagaModel).where(
                    ReservaItemSagaModel.operation_id == UUID(primeira.operation_id)
                )
            )
        ).scalar_one()
        outbox = (
            await session.exec(
                select(OutboxMessageModel).where(
                    OutboxMessageModel.id == saga.ultimo_comando_message_id
                )
            )
        ).scalar_one()
        ordem = await session.get(OrdemServicoModel, ordem_id)
        saga_count = await session.scalar(select(func.count()).select_from(ReservaItemSagaModel))
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxMessageModel))
        item_count = await session.scalar(select(func.count()).select_from(OrdemServicoItemModel))

    assert saga_count == 1
    assert outbox_count == 1
    assert item_count == 0
    assert ordem is not None and ordem.version == 2
    assert outbox.aggregate_id == saga.operation_id
    assert outbox.saga_id == saga.id
    assert outbox.correlation_id == correlation_id
    assert outbox.published_at is None


async def test_payload_divergente_preserva_primeira_operacao(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
    resource_id = uuid4()
    def usecase() -> AdicionarItemNaOrdemServicoUseCase:
        return AdicionarItemNaOrdemServicoUseCase(
            uow=OrdemServicoUnitOfWork(factory()),
            recursos=_CatalogoFake(_snapshot(resource_id)),
        )
    await usecase().execute(
        str(ordem_id),
        AdicionarItemNaOSRequest(item_estoque_id=str(resource_id), quantidade=1),
        idempotency_key="request-conflict-123",
        correlation_id=uuid4(),
    )

    with pytest.raises(ReservaItemSagaIdempotenciaConflitanteError):
        await usecase().execute(
            str(ordem_id),
            AdicionarItemNaOSRequest(item_estoque_id=str(resource_id), quantidade=2),
            idempotency_key="request-conflict-123",
            correlation_id=uuid4(),
        )

    async with factory() as session:
        saga = (await session.exec(select(ReservaItemSagaModel))).scalar_one()
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxMessageModel))
    assert saga.quantity == 1
    assert outbox_count == 1


async def test_falha_na_outbox_reverte_saga_e_versao_da_os(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
    resource_id = uuid4()
    uow = OrdemServicoUnitOfWork(factory())
    uow.outbox.add = AsyncMock(side_effect=RuntimeError("falha injetada na Outbox"))

    with pytest.raises(RuntimeError, match="falha injetada"):
        await AdicionarItemNaOrdemServicoUseCase(
            uow=uow,
            recursos=_CatalogoFake(_snapshot(resource_id)),
        ).execute(
            str(ordem_id),
            AdicionarItemNaOSRequest(item_estoque_id=str(resource_id), quantidade=1),
            idempotency_key="request-rollback-123",
            correlation_id=uuid4(),
        )

    async with factory() as session:
        saga_count = await session.scalar(select(func.count()).select_from(ReservaItemSagaModel))
        outbox_count = await session.scalar(select(func.count()).select_from(OutboxMessageModel))
        ordem = await session.get(OrdemServicoModel, ordem_id)
    assert saga_count == 0
    assert outbox_count == 0
    assert ordem is not None and ordem.version == 1
