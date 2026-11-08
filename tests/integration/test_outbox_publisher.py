"""Publisher exercitado contra PostgreSQL 16 e ElasticMQ reais."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.outbox_publisher import (
    OutboxPublisher,
    PublisherPolicy,
    StaticOrdensMessageRouter,
)
from app.infrastructure.messaging.contracts import JsonSchemaMessageValidator
from app.infrastructure.messaging.outbox_store import PostgresPublisherOutboxStore
from app.infrastructure.messaging.sqs import SqsFifoTransport, create_sqs_client
from app.shared.infra.db.outbox_model import OutboxMessageModel
from app.shared.infra.db.outbox_repository import PostgresOutboxRepository
from app.shared.outbox import OutboxMessage

QUEUE_URL = os.environ["SQS_ORDENS_TO_RECURSOS_URL"]
ENDPOINT_URL = os.environ["SQS_ENDPOINT_URL"]
QUEUE_NAME = "mvp-oficina-ordens-to-recursos-local.fifo"


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _record(*, event_type: str = "ReserveItem.v1", valid: bool = True) -> OutboxMessage:
    operation_id = uuid4()
    payload = {
        "order_id": str(uuid4()),
        "order_item_id": str(uuid4()),
        "resource_id": str(uuid4()),
        "quantity": 2 if valid else 0,
        "resource_snapshot": {
            "name": "Filtro de óleo",
            "description": "Snapshot da ordem",
            "unit_price": "39.90",
        },
    }
    return OutboxMessage(
        message_id=uuid4(),
        aggregate_type="StockOperation",
        aggregate_id=operation_id,
        event_type=event_type,
        event_version=1,
        payload=payload,
        correlation_id=uuid4(),
        causation_id=uuid4(),
        saga_id=uuid4(),
        occurred_at=datetime.now(UTC),
    )


@pytest.fixture
def sqs_client():
    client = boto3.client(
        "sqs",
        endpoint_url=ENDPOINT_URL,
        region_name="us-east-1",
        config=Config(retries={"max_attempts": 1}),
    )
    try:
        client.get_queue_url(QueueName=QUEUE_NAME)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "AWS.SimpleQueueService.NonExistentQueue",
            "QueueDoesNotExist",
        }:
            raise
        client.create_queue(
            QueueName=QUEUE_NAME,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )
    while True:
        result = client.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=10)
        messages = result.get("Messages", [])
        if not messages:
            break
        for message in messages:
            client.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])
    yield client
    client.close()


def _publisher(engine: AsyncEngine, client, *, worker_id: str = "publisher-integration"):
    return OutboxPublisher(
        worker_id=worker_id,
        store=PostgresPublisherOutboxStore(_factory(engine)),
        router=StaticOrdensMessageRouter(QUEUE_URL),
        validator=JsonSchemaMessageValidator("contracts/messages/saga-stock.v1.schema.json"),
        transport=SqsFifoTransport(client, send_timeout_seconds=2),
        policy=PublisherPolicy(
            batch_size=10,
            lease_seconds=10,
            poll_interval_seconds=0.1,
            max_attempts=8,
            initial_backoff_seconds=1,
            max_backoff_seconds=60,
        ),
        jitter=lambda _start, cap: cap,
    )


async def _save(engine: AsyncEngine, *records: OutboxMessage) -> None:
    async with _factory(engine)() as session:
        repository = PostgresOutboxRepository(session)
        for record in records:
            await repository.add(record)
        await session.commit()


async def test_publica_no_elasticmq_com_envelope_e_atributos_fifo(
    db_engine: AsyncEngine,
    sqs_client,
) -> None:
    record = _record()
    await _save(db_engine, record)

    assert await _publisher(db_engine, sqs_client).run_once() == 1

    response = sqs_client.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=1,
        AttributeNames=["All"],
    )
    [received] = response["Messages"]
    envelope = json.loads(received["Body"])
    assert envelope["message_id"] == str(record.message_id)
    assert envelope["message_type"] == "ReserveItem"
    assert envelope["correlation_id"] == str(record.correlation_id)
    assert envelope["causation_id"] == str(record.causation_id)
    assert envelope["operation_id"] == str(record.aggregate_id)
    assert envelope["saga_id"] == str(record.saga_id)
    assert received["Attributes"]["MessageDeduplicationId"] == str(record.message_id)
    assert received["Attributes"]["MessageGroupId"] == str(record.aggregate_id)

    async with _factory(db_engine)() as session:
        stored = await session.get(OutboxMessageModel, record.message_id)
        assert stored is not None
        assert stored.published_at is not None
        assert stored.claim_id is None


async def test_falha_real_de_conexao_mantem_pendente_e_agenda_retry(
    db_engine: AsyncEngine,
) -> None:
    record = _record()
    await _save(db_engine, record)
    unavailable_client = create_sqs_client(
        region="us-east-1",
        endpoint_url="http://127.0.0.1:1",
        connect_timeout_seconds=0.1,
        read_timeout_seconds=0.1,
    )
    publisher = _publisher(db_engine, unavailable_client)

    assert await publisher.run_once() == 1
    unavailable_client.close()

    async with _factory(db_engine)() as session:
        stored = await session.get(OutboxMessageModel, record.message_id)
        assert stored is not None
        assert stored.published_at is None
        assert stored.claim_id is None
        assert stored.attempts == 1
        assert stored.last_error is not None
        assert stored.next_attempt_at > stored.last_failed_at


async def test_invalidas_e_sem_rota_nao_sao_descartadas_silenciosamente(
    db_engine: AsyncEngine,
    sqs_client,
) -> None:
    invalid = _record(valid=False)
    unrouted = _record(event_type="ResourceReserved.v1")
    await _save(db_engine, invalid, unrouted)

    assert await _publisher(db_engine, sqs_client).run_once() == 2

    assert not sqs_client.receive_message(QueueUrl=QUEUE_URL).get("Messages")
    async with _factory(db_engine)() as session:
        invalid_stored = await session.get(OutboxMessageModel, invalid.message_id)
        unrouted_stored = await session.get(OutboxMessageModel, unrouted.message_id)
        assert invalid_stored is not None and invalid_stored.published_at is None
        assert invalid_stored.last_error.startswith("MessageContractError:")
        assert unrouted_stored is not None and unrouted_stored.published_at is None
        assert unrouted_stored.last_error.startswith("UnknownMessageRouteError:")


class ConfirmationFailureStore(PostgresPublisherOutboxStore):
    async def mark_published(self, **_kwargs) -> None:
        raise RuntimeError("falha injetada depois do envio")


async def test_falha_de_confirmacao_recupera_lease_sem_perder_mensagem(
    db_engine: AsyncEngine,
    sqs_client,
) -> None:
    record = _record()
    await _save(db_engine, record)
    publisher = _publisher(db_engine, sqs_client)
    publisher.store = ConfirmationFailureStore(_factory(db_engine))

    assert await publisher.run_once() == 1
    async with _factory(db_engine)() as session:
        stored = await session.get(OutboxMessageModel, record.message_id)
        assert stored is not None
        assert stored.published_at is None
        assert stored.claim_id is not None
        forced_now = datetime.now(UTC)
        await session.exec(
            update(OutboxMessageModel)
            .where(OutboxMessageModel.id == record.message_id)
            .values(
                claimed_at=forced_now - timedelta(seconds=2),
                claim_expires_at=forced_now - timedelta(seconds=1),
            )
        )
        await session.commit()

    restarted = _publisher(db_engine, sqs_client, worker_id="publisher-restarted")
    assert await restarted.run_once() == 1
    async with _factory(db_engine)() as session:
        recovered = await session.get(OutboxMessageModel, record.message_id)
        assert recovered is not None
        assert recovered.published_at is not None
        assert recovered.attempts == 2


async def test_publishers_concorrentes_reivindicam_lotes_disjuntos(
    db_engine: AsyncEngine,
    sqs_client,
) -> None:
    records = [_record() for _ in range(4)]
    await _save(db_engine, *records)
    publisher_a = _publisher(db_engine, sqs_client, worker_id="publisher-a")
    publisher_b = _publisher(db_engine, sqs_client, worker_id="publisher-b")

    counts = await asyncio.gather(
        publisher_a.run_once(),
        publisher_b.run_once(),
    )

    assert sum(counts) == 4
    async with _factory(db_engine)() as session:
        stored = [await session.get(OutboxMessageModel, item.message_id) for item in records]
    assert all(item is not None and item.published_at is not None for item in stored)
