import asyncio
import json
from uuid import uuid4

import pytest

from app.application.inbox_consumer import (
    ConsumerPolicy,
    InboxConsumer,
    ProcessingOutcome,
    ReceivedMessage,
)


def envelope(message_type="ResourceReserved"):
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": "2026-11-09T12:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "operation_id": str(uuid4()),
        "saga_id": str(uuid4()),
        "payload": {},
    }


class Validator:
    def __init__(self, error=None):
        self.error = error

    def validate(self, _value):
        if self.error:
            raise self.error


class Processor:
    def __init__(self, outcome=ProcessingOutcome.PROCESSED, error=None, delay=0):
        self.outcome, self.error, self.delay = outcome, error, delay
        self.calls = 0

    async def process(self, _value, *, receive_count):
        self.calls += 1
        assert receive_count == 2
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.outcome


class Transport:
    def __init__(self, messages, delete_error=None):
        self.messages, self.delete_error = messages, delete_error
        self.deleted, self.extended = [], []

    async def receive(self, **_kwargs):
        result, self.messages = self.messages, []
        return result

    async def delete(self, receipt):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(receipt)

    async def extend_visibility(self, receipt, **_kwargs):
        self.extended.append(receipt)


def received(value=None):
    return ReceivedMessage("receipt", json.dumps(value or envelope()), 2, "sqs")


def consumer(transport, processor, validator=None, heartbeat=0.5):
    return InboxConsumer(
        consumer_name="ordens.resource-results.v1",
        allowed_message_types=frozenset({"ResourceReserved"}),
        validator=validator or Validator(),
        processor=processor,
        transport=transport,
        policy=ConsumerPolicy(10, 2, 0, 1, heartbeat),
    )


async def test_ack_depois_do_sucesso_e_heartbeat():
    transport, processor = Transport([received()]), Processor(delay=0.04)
    await consumer(transport, processor, heartbeat=0.01).run_once()
    assert processor.calls == 1 and transport.deleted == ["receipt"] and transport.extended


@pytest.mark.parametrize(
    ("processor", "validator"),
    [
        (Processor(error=RuntimeError("db")), Validator()),
        (Processor(), Validator(ValueError("schema"))),
        (Processor(ProcessingOutcome.CONFLICT), Validator()),
    ],
)
async def test_falha_conflito_ou_schema_invalido_nao_faz_ack(processor, validator):
    transport = Transport([received()])
    await consumer(transport, processor, validator).run_once()
    assert transport.deleted == []


async def test_tipo_de_outra_fila_nao_processa():
    processor = Processor()
    transport = Transport([received(envelope("QuoteApproved"))])
    await consumer(transport, processor).run_once()
    assert processor.calls == 0 and transport.deleted == []


async def test_falha_de_ack_permite_reentrega_deduplicada():
    transport = Transport([received()], RuntimeError("indeterminado"))
    await consumer(transport, Processor(ProcessingOutcome.DUPLICATE)).run_once()
    assert transport.deleted == []
