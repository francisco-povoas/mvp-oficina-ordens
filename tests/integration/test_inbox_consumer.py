import asyncio
import json
import os
from uuid import uuid4

import boto3
import pytest
import sqlalchemy as sa
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.inbox_consumer import ConsumerPolicy, InboxConsumer
from app.consumer import (
    FINANCIAL_CONSUMER,
    FINANCIAL_TYPES,
    RESOURCE_CONSUMER,
    RESOURCE_TYPES,
)
from app.infrastructure.messaging.contracts import JsonSchemaMessageValidator
from app.infrastructure.messaging.inbox import PostgresInboxProcessor
from app.infrastructure.messaging.sqs_consumer import SqsConsumerTransport

ENDPOINT = os.environ["SQS_ENDPOINT_URL"]
RESOURCE_URL = os.environ["SQS_RECURSOS_TO_ORDENS_URL"]
FINANCIAL_URL = os.environ["SQS_FINANCEIRO_TO_ORDENS_URL"]


def resource_envelope():
    return {
        "message_id": str(uuid4()),
        "message_type": "ResourceReserved",
        "message_version": 1,
        "occurred_at": "2026-11-09T12:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "saga_id": str(uuid4()),
        "payload": {
            "reservation_id": str(uuid4()),
            "resource_id": str(uuid4()),
            "quantity": 1,
            "reserved_at": "2026-11-09T12:00:00Z",
            "resource_snapshot": {"name": "Filtro", "description": None, "unit_price": "10.00"},
        },
    }


def financial_envelope():
    return {
        "message_id": str(uuid4()),
        "message_type": "QuoteApproved",
        "message_version": 1,
        "occurred_at": "2026-11-09T12:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "saga_id": None,
        "payload": {
            "order_id": str(uuid4()),
            "quote_id": str(uuid4()),
            "quote_version": 1,
            "approved_at": "2026-11-09T12:00:00Z",
        },
    }


def ensure_queue(client, name, attrs):
    try:
        return client.get_queue_url(QueueName=name)["QueueUrl"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {
            "AWS.SimpleQueueService.NonExistentQueue",
            "QueueDoesNotExist",
        }:
            raise
    return client.create_queue(QueueName=name, Attributes=attrs)["QueueUrl"]


def drain(client, url):
    while True:
        messages = client.receive_message(
            QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=0
        ).get("Messages", [])
        if not messages:
            return
        for message in messages:
            client.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])


@pytest.fixture
def queues():
    client = boto3.client(
        "sqs", endpoint_url=ENDPOINT, region_name="us-east-1", config=Config(retries={"max_attempts": 1})
    )
    urls = {}
    for prefix in ("recursos", "financeiro"):
        dlq_name = f"mvp-oficina-{prefix}-to-ordens-local-dlq.fifo"
        queue_name = f"mvp-oficina-{prefix}-to-ordens-local.fifo"
        dlq = ensure_queue(
            client, dlq_name, {"FifoQueue": "true", "ContentBasedDeduplication": "false"}
        )
        arn = client.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        queue = ensure_queue(
            client,
            queue_name,
            {
                "FifoQueue": "true",
                "ContentBasedDeduplication": "false",
                "VisibilityTimeout": "1",
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": arn, "maxReceiveCount": "5"}
                ),
            },
        )
        drain(client, queue)
        drain(client, dlq)
        urls[prefix] = (queue, dlq)
    yield client, urls
    for pair in urls.values():
        drain(client, pair[0])
        drain(client, pair[1])
    client.close()


@pytest.fixture
async def probe(db_engine):
    async with db_engine.begin() as connection:
        await connection.execute(sa.text("CREATE TABLE consumer_transport_probe (id UUID PRIMARY KEY)"))
    yield
    async with db_engine.begin() as connection:
        await connection.execute(sa.text("DROP TABLE consumer_transport_probe"))


class Handler:
    def __init__(self):
        self.calls = 0

    async def handle(self, value, session):
        self.calls += 1
        await session.exec(
            sa.text("INSERT INTO consumer_transport_probe (id) VALUES (:id)"),
            params={"id": value["message_id"]},
        )


def consumer(engine, client, *, name, types, schema, queue_url, handler):
    return InboxConsumer(
        consumer_name=name,
        allowed_message_types=types,
        validator=JsonSchemaMessageValidator(schema),
        processor=PostgresInboxProcessor(
            async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
            consumer_name=name,
            handler=handler,
        ),
        transport=SqsConsumerTransport(client, queue_url=queue_url),
        policy=ConsumerPolicy(10, 2, 0, 1, 0.2),
    )


@pytest.mark.parametrize("source", ["recursos", "financeiro"])
async def test_duas_filas_processam_commit_e_ack(db_engine, probe, queues, source):
    client, urls = queues
    if source == "recursos":
        value, name, types, schema = (
            resource_envelope(),
            RESOURCE_CONSUMER,
            RESOURCE_TYPES,
            "contracts/messages/saga-stock.v1.schema.json",
        )
    else:
        value, name, types, schema = (
            financial_envelope(),
            FINANCIAL_CONSUMER,
            FINANCIAL_TYPES,
            "contracts/vendor/financeiro/financial-events.v1.schema.json",
        )
    queue_url, _ = urls[source]
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(value),
        MessageGroupId=value["operation_id"],
        MessageDeduplicationId=value["message_id"],
    )
    handler = Handler()
    worker = consumer(
        db_engine,
        client,
        name=name,
        types=types,
        schema=schema,
        queue_url=queue_url,
        handler=handler,
    )
    assert await worker.run_once() == 1
    assert handler.calls == 1
    assert not client.receive_message(QueueUrl=queue_url).get("Messages")


async def test_financeiro_invalido_nao_faz_ack_e_vai_para_dlq(db_engine, probe, queues):
    client, urls = queues
    queue_url, dlq_url = urls["financeiro"]
    body = '{"message_type":"QuoteApproved"}'
    client.send_message(
        QueueUrl=queue_url,
        MessageBody=body,
        MessageGroupId=str(uuid4()),
        MessageDeduplicationId=str(uuid4()),
    )
    worker = consumer(
        db_engine,
        client,
        name=FINANCIAL_CONSUMER,
        types=FINANCIAL_TYPES,
        schema="contracts/vendor/financeiro/financial-events.v1.schema.json",
        queue_url=queue_url,
        handler=Handler(),
    )
    assert await worker.run_once() == 1
    await asyncio.sleep(1.1)
    for _ in range(10):
        if not client.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=1, VisibilityTimeout=0
        ).get("Messages", []):
            break
    dead = client.receive_message(
        QueueUrl=dlq_url, MaxNumberOfMessages=1, VisibilityTimeout=0
    ).get("Messages", [])
    assert dead and dead[0]["Body"] == body
