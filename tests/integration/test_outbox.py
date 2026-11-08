"""Outbox funcional validada contra PostgreSQL real."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.clientes.domain.entities.cliente import Cliente
from app.modules.clientes.domain.value_objects.cpf_cnpj import CpfCnpj
from app.modules.clientes.domain.value_objects.telefone import Telefone
from app.modules.clientes.domain.value_objects.tipo_pessoa import TipoPessoa
from app.modules.clientes.infrastructure.db.repositories.cliente_repo import ClienteRepo
from app.modules.clientes.infrastructure.db.uow.cliente_uow import ClienteUnitOfWork
from app.shared.infra.db.outbox_model import OutboxMessageModel
from app.shared.infra.db.outbox_repository import PostgresOutboxRepository
from app.shared.outbox import OutboxClaimConflictError, OutboxMessage
from app.shared.value_objects.email import Email
from app.shared.value_objects.id import ID


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _cliente(*, cpf: str) -> Cliente:
    now = datetime.now(UTC)
    return Cliente(
        id=ID.generate(),
        tipo_pessoa=TipoPessoa.PF,
        nome_razao_social="Cliente da Outbox",
        cpf_cnpj=CpfCnpj(cpf),
        telefone=Telefone("71999999999"),
        email=Email("outbox@example.com"),
        cep="40000000",
        logradouro="Rua Transacional",
        numero="1",
        complemento=None,
        bairro="Centro",
        cidade="Salvador",
        uf="BA",
        ativo=True,
        criado_em=now,
        atualizado_em=now,
    )


def _message(*, aggregate_id, occurred_at: datetime, message_id=None) -> OutboxMessage:
    return OutboxMessage(
        message_id=message_id or uuid4(),
        aggregate_type="OrdemServico",
        aggregate_id=aggregate_id,
        event_type="ReserveItem.v1",
        event_version=1,
        payload={"quantity": 1, "resource_id": str(uuid4())},
        correlation_id=uuid4(),
        causation_id=None,
        occurred_at=occurred_at,
    )


async def _persist_messages(
    engine: AsyncEngine,
    *,
    count: int,
    occurred_at: datetime,
) -> list[OutboxMessage]:
    messages = [
        _message(aggregate_id=uuid4(), occurred_at=occurred_at) for _ in range(count)
    ]
    factory = _factory(engine)
    async with factory() as session:
        repository = PostgresOutboxRepository(session)
        for message in messages:
            await repository.add(message)
        await session.commit()
    return messages


async def test_mudanca_de_negocio_e_outbox_confirmam_na_mesma_transacao(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    cliente = _cliente(cpf="12345678901")
    now = datetime.now(UTC)
    message = _message(aggregate_id=cliente.id.value, occurred_at=now)

    async with ClienteUnitOfWork(factory()) as uow:
        await uow.cliente_repo.salvar(cliente)
        await uow.outbox.add(message)

    async with factory() as session:
        assert await ClienteRepo(session).obter_por_id(cliente.id) == cliente
        stored = await session.get(OutboxMessageModel, message.message_id)

    assert stored is not None
    assert stored.payload == '{"quantity":1,"resource_id":"' + str(
        message.payload["resource_id"]
    ) + '"}'
    assert stored.next_attempt_at == now


async def test_excecao_reverte_mudanca_de_negocio_e_outbox(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    cliente = _cliente(cpf="98765432100")
    message = _message(aggregate_id=cliente.id.value, occurred_at=datetime.now(UTC))

    with pytest.raises(RuntimeError, match="falha injetada"):
        async with ClienteUnitOfWork(factory()) as uow:
            await uow.cliente_repo.salvar(cliente)
            await uow.outbox.add(message)
            raise RuntimeError("falha injetada")

    async with factory() as session:
        assert await ClienteRepo(session).obter_por_id(cliente.id) is None
        assert await session.get(OutboxMessageModel, message.message_id) is None


async def test_falha_no_commit_da_outbox_nao_deixa_mudanca_parcial(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    duplicate_id = uuid4()
    now = datetime.now(UTC)
    async with factory() as session:
        await PostgresOutboxRepository(session).add(
            _message(aggregate_id=uuid4(), occurred_at=now, message_id=duplicate_id)
        )
        await session.commit()

    cliente = _cliente(cpf="11122233344")
    with pytest.raises(IntegrityError):
        async with ClienteUnitOfWork(factory()) as uow:
            await uow.cliente_repo.salvar(cliente)
            await uow.outbox.add(
                _message(
                    aggregate_id=cliente.id.value,
                    occurred_at=now,
                    message_id=duplicate_id,
                )
            )

    async with factory() as session:
        assert await ClienteRepo(session).obter_por_id(cliente.id) is None


async def test_claim_concorrente_usa_skip_locked_sem_duplicar_lote(
    db_engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    messages = await _persist_messages(db_engine, count=4, occurred_at=now)
    factory = _factory(db_engine)

    async with factory() as session_a, factory() as session_b:
        claims_a = await PostgresOutboxRepository(session_a).claim_pending(
            worker_id="publisher-a",
            batch_size=2,
            lease_duration=timedelta(seconds=30),
            now=now,
        )
        claims_b = await PostgresOutboxRepository(session_b).claim_pending(
            worker_id="publisher-b",
            batch_size=4,
            lease_duration=timedelta(seconds=30),
            now=now,
        )
        await session_b.commit()
        await session_a.commit()

    ids_a = {claim.message_id for claim in claims_a}
    ids_b = {claim.message_id for claim in claims_b}
    assert len(ids_a) == 2
    assert len(ids_b) == 2
    assert ids_a.isdisjoint(ids_b)
    assert ids_a | ids_b == {message.message_id for message in messages}


async def test_claim_expirado_e_recuperado_e_invalida_worker_antigo(
    db_engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    [message] = await _persist_messages(db_engine, count=1, occurred_at=now)
    factory = _factory(db_engine)

    async with factory() as session:
        repository = PostgresOutboxRepository(session)
        [first_claim] = await repository.claim_pending(
            worker_id="publisher-antigo",
            batch_size=1,
            lease_duration=timedelta(seconds=10),
            now=now,
        )
        await session.commit()

    async with factory() as session:
        assert not await PostgresOutboxRepository(session).claim_pending(
            worker_id="publisher-cedo",
            batch_size=1,
            lease_duration=timedelta(seconds=10),
            now=now + timedelta(seconds=9),
        )
        await session.rollback()

    recovered_at = now + timedelta(seconds=11)
    async with factory() as session:
        repository = PostgresOutboxRepository(session)
        [recovered_claim] = await repository.claim_pending(
            worker_id="publisher-recuperador",
            batch_size=1,
            lease_duration=timedelta(seconds=10),
            now=recovered_at,
        )
        await session.commit()

    assert recovered_claim.message_id == message.message_id
    assert recovered_claim.claim_id != first_claim.claim_id
    assert recovered_claim.attempts == 2

    async with factory() as session:
        with pytest.raises(OutboxClaimConflictError):
            await PostgresOutboxRepository(session).mark_published(
                message_id=message.message_id,
                claim_id=first_claim.claim_id,
                published_at=recovered_at + timedelta(seconds=1),
            )
        await session.rollback()


async def test_falha_agenda_retry_e_confirmacao_remove_pendencia(
    db_engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    [message] = await _persist_messages(db_engine, count=1, occurred_at=now)
    factory = _factory(db_engine)
    retry_at = now + timedelta(minutes=1)

    async with factory() as session:
        repository = PostgresOutboxRepository(session)
        [claim] = await repository.claim_pending(
            worker_id="publisher",
            batch_size=1,
            lease_duration=timedelta(seconds=30),
            now=now,
        )
        await repository.mark_failed(
            message_id=message.message_id,
            claim_id=claim.claim_id,
            error="ElasticMQ indisponivel",
            failed_at=now + timedelta(seconds=1),
            next_attempt_at=retry_at,
        )
        await session.commit()

    async with factory() as session:
        repository = PostgresOutboxRepository(session)
        assert not await repository.claim_pending(
            worker_id="publisher-cedo",
            batch_size=1,
            lease_duration=timedelta(seconds=30),
            now=retry_at - timedelta(microseconds=1),
        )
        await session.rollback()

    async with factory() as session:
        repository = PostgresOutboxRepository(session)
        [retry_claim] = await repository.claim_pending(
            worker_id="publisher-retry",
            batch_size=1,
            lease_duration=timedelta(seconds=30),
            now=retry_at,
        )
        published_at = retry_at + timedelta(seconds=1)
        await repository.mark_published(
            message_id=message.message_id,
            claim_id=retry_claim.claim_id,
            published_at=published_at,
        )
        await session.commit()

    async with factory() as session:
        stored = await session.get(OutboxMessageModel, message.message_id)
        assert stored is not None
        assert stored.attempts == 2
        assert stored.published_at == published_at
        assert stored.last_error == "ElasticMQ indisponivel"
        assert stored.last_failed_at == now + timedelta(seconds=1)
        assert stored.claim_id is None
        assert not await PostgresOutboxRepository(session).claim_pending(
            worker_id="publisher-depois",
            batch_size=1,
            lease_duration=timedelta(seconds=30),
            now=published_at + timedelta(minutes=1),
        )
