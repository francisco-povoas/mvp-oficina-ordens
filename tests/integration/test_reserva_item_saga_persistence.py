"""Persistência, rollback e concorrência da Saga em PostgreSQL real."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    EstadoReservaItemSaga,
    ReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ReservaItemSagaConflitoDeVersaoError,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)


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
            "VALUES (:id, 'PF', 'Cliente Saga', :cpf, '71999999999', true, :agora, :agora)"
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
            "placa": f"S{str(veiculo_id).replace('-', '')[:6]}",
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
    await session.flush()
    return ordem_id


def _saga(ordem_id: UUID, *, key: str = "request-123") -> ReservaItemSaga:
    return ReservaItemSaga.iniciar(
        saga_id=uuid4(),
        operation_id=uuid4(),
        ordem_servico_id=ordem_id,
        ordem_servico_item_id=uuid4(),
        resource_id=uuid4(),
        quantity=2,
        correlation_id=uuid4(),
        idempotency_key=key,
        request_hash="a" * 64,
        reserve_message_id=uuid4(),
    )


async def test_cria_recupera_e_lista_saga_nao_terminal(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
        saga = _saga(ordem_id)
        await ReservaItemSagaRepo(session).salvar(saga)
        await session.commit()

    async with factory() as session:
        repo = ReservaItemSagaRepo(session)
        recuperada = await repo.obter_por_operation_id(saga.operation_id)
        por_chave = await repo.obter_por_chave_idempotencia(
            ordem_servico_id=ordem_id,
            idempotency_key=saga.idempotency_key,
        )
        pendentes = await repo.listar_nao_terminais_da_os(ordem_id)

    assert recuperada == saga
    assert por_chave == saga
    assert pendentes == [saga]


async def test_controle_otimista_rejeita_lost_update(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
        saga = _saga(ordem_id)
        await ReservaItemSagaRepo(session).salvar(saga)
        await session.commit()

    async with factory() as session_a, factory() as session_b:
        repo_a = ReservaItemSagaRepo(session_a)
        repo_b = ReservaItemSagaRepo(session_b)
        saga_a = await repo_a.obter_por_id(saga.id)
        saga_b = await repo_b.obter_por_id(saga.id)
        assert saga_a is not None and saga_b is not None

        await repo_a.atualizar(saga_a.solicitar_cancelamento())
        await session_a.commit()

        with pytest.raises(ReservaItemSagaConflitoDeVersaoError):
            await repo_b.atualizar(saga_b.exigir_reconciliacao())
        await session_b.rollback()

    async with factory() as session:
        recuperada = await ReservaItemSagaRepo(session).obter_por_id(saga.id)

    assert recuperada is not None
    assert recuperada.estado is EstadoReservaItemSaga.CANCEL_REQUESTED
    assert recuperada.version == 2


async def test_uow_reverte_saga_sem_efeito_parcial(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
        await session.commit()
    saga = _saga(ordem_id)

    with pytest.raises(RuntimeError, match="rollback intencional"):
        async with OrdemServicoUnitOfWork(factory()) as uow:
            await uow.reserva_item_saga_repo.salvar(saga)
            raise RuntimeError("rollback intencional")

    async with factory() as session:
        assert await ReservaItemSagaRepo(session).obter_por_id(saga.id) is None


async def test_chave_idempotente_e_operation_id_sao_unicos(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        ordem_id = await _persistir_ordem(session)
        original = _saga(ordem_id)
        await ReservaItemSagaRepo(session).salvar(original)
        await session.commit()

    async with factory() as session:
        duplicada = _saga(ordem_id, key=original.idempotency_key)
        await ReservaItemSagaRepo(session).salvar(duplicada)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with factory() as session:
        outra = _saga(ordem_id, key="request-456")
        outra = ReservaItemSaga(
            **{**outra.__dict__, "operation_id": original.operation_id}
        )
        await ReservaItemSagaRepo(session).salvar(outra)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
