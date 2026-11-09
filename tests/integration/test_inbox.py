import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.inbox_consumer import ProcessingOutcome
from app.consumer import FINANCIAL_CONSUMER, RESOURCE_CONSUMER
from app.infrastructure.messaging.inbox import PostgresInboxProcessor


def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def resource_envelope(message_type="ResourceReserved", *, operation_id=None):
    operation_id = str(operation_id or uuid4())
    payloads = {
        "ResourceReserved": {
            "reservation_id": str(uuid4()),
            "resource_id": str(uuid4()),
            "quantity": 2,
            "reserved_at": "2026-11-09T12:00:00Z",
            "resource_snapshot": {"name": "Filtro", "description": None, "unit_price": "20.00"},
        },
        "ItemBackordered": {
            "resource_id": str(uuid4()),
            "requested_quantity": 2,
            "available_quantity": 0,
            "decided_at": "2026-11-09T12:00:00Z",
            "resource_snapshot": {"name": "Filtro", "description": None, "unit_price": "20.00"},
        },
    }
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": "2026-11-09T12:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "operation_id": operation_id,
        "saga_id": str(uuid4()),
        "payload": payloads[message_type],
    }


def financial_envelope(message_type="QuoteApproved", *, quote_id=None, operation_id=None):
    quote_id = str(quote_id or uuid4())
    payload = {
        "order_id": str(uuid4()),
        "quote_id": quote_id,
        "quote_version": 1,
        "approved_at": "2026-11-09T12:00:00Z",
    }
    if message_type == "QuoteRejected":
        payload.pop("approved_at")
        payload.update(reason="cliente recusou", rejected_at="2026-11-09T12:00:00Z")
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": "2026-11-09T12:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "operation_id": str(operation_id or uuid4()),
        "saga_id": None,
        "payload": payload,
    }


@pytest.fixture
async def probe_table(db_engine):
    async with db_engine.begin() as connection:
        await connection.execute(
            sa.text("CREATE TABLE consumer_effect_probe (business_key VARCHAR(220) PRIMARY KEY)")
        )
    yield
    async with db_engine.begin() as connection:
        await connection.execute(sa.text("DROP TABLE consumer_effect_probe"))


class Handler:
    def __init__(self, *, fail=False, delay=0):
        self.fail, self.delay, self.calls = fail, delay, 0

    async def handle(self, value, session):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        key = f"{value['message_type']}:{value['operation_id']}"
        await session.exec(
            sa.text("INSERT INTO consumer_effect_probe (business_key) VALUES (:key)"),
            params={"key": key},
        )
        if self.fail:
            raise RuntimeError("falha depois do efeito")


async def count(engine, table):
    async with factory(engine)() as session:
        return (await session.exec(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one()


def processor(engine, consumer_name, handler):
    return PostgresInboxProcessor(factory(engine), consumer_name=consumer_name, handler=handler)


async def test_message_id_reentregue_e_nova_emissao_mesmo_efeito_deduplicam(
    db_engine, probe_table
):
    handler, first = Handler(), resource_envelope()
    second = deepcopy(first)
    second["message_id"], second["correlation_id"] = str(uuid4()), str(uuid4())
    inbox = processor(db_engine, RESOURCE_CONSUMER, handler)
    assert await inbox.process(first, receive_count=1) is ProcessingOutcome.PROCESSED
    assert await inbox.process(first, receive_count=2) is ProcessingOutcome.DUPLICATE
    assert await inbox.process(second, receive_count=1) is ProcessingOutcome.DUPLICATE
    assert handler.calls == 1 and await count(db_engine, "inbox_message") == 2


async def test_resultados_alternativos_da_reserva_conflitam(db_engine, probe_table):
    handler, operation_id = Handler(), uuid4()
    reserved = resource_envelope("ResourceReserved", operation_id=operation_id)
    backordered = resource_envelope("ItemBackordered", operation_id=operation_id)
    inbox = processor(db_engine, RESOURCE_CONSUMER, handler)
    assert await inbox.process(reserved, receive_count=1) is ProcessingOutcome.PROCESSED
    assert await inbox.process(backordered, receive_count=1) is ProcessingOutcome.CONFLICT
    assert handler.calls == 1


async def test_aprovacao_e_recusa_da_mesma_versao_conflitam(db_engine, probe_table):
    handler, quote_id = Handler(), uuid4()
    approved = financial_envelope("QuoteApproved", quote_id=quote_id)
    rejected = financial_envelope("QuoteRejected", quote_id=quote_id)
    inbox = processor(db_engine, FINANCIAL_CONSUMER, handler)
    assert await inbox.process(approved, receive_count=1) is ProcessingOutcome.PROCESSED
    assert await inbox.process(rejected, receive_count=1) is ProcessingOutcome.CONFLICT
    assert handler.calls == 1


async def test_operation_id_nao_e_chave_universal_entre_consumers(db_engine, probe_table):
    shared = uuid4()
    resource, financial = resource_envelope(operation_id=shared), financial_envelope(operation_id=shared)
    resource_handler, financial_handler = Handler(), Handler()
    assert await processor(db_engine, RESOURCE_CONSUMER, resource_handler).process(
        resource, receive_count=1
    ) is ProcessingOutcome.PROCESSED
    assert await processor(db_engine, FINANCIAL_CONSUMER, financial_handler).process(
        financial, receive_count=1
    ) is ProcessingOutcome.PROCESSED
    assert resource_handler.calls == financial_handler.calls == 1


async def test_falha_reverte_efeito_e_inbox_e_restart_recupera(db_engine, probe_table):
    value = resource_envelope()
    with pytest.raises(RuntimeError, match="falha depois"):
        await processor(db_engine, RESOURCE_CONSUMER, Handler(fail=True)).process(
            value, receive_count=1
        )
    assert await count(db_engine, "consumer_effect_probe") == 0
    assert await count(db_engine, "inbox_message") == 0
    recovered = Handler()
    assert await processor(db_engine, RESOURCE_CONSUMER, recovered).process(
        value, receive_count=2
    ) is ProcessingOutcome.PROCESSED


async def test_consumers_concorrentes_executam_um_efeito(db_engine, probe_table):
    first = resource_envelope()
    second = deepcopy(first)
    second["message_id"] = str(uuid4())
    handler = Handler(delay=0.05)
    outcomes = await asyncio.gather(
        processor(db_engine, RESOURCE_CONSUMER, handler).process(first, receive_count=1),
        processor(db_engine, RESOURCE_CONSUMER, handler).process(second, receive_count=1),
    )
    assert set(outcomes) == {ProcessingOutcome.PROCESSED, ProcessingOutcome.DUPLICATE}
    assert handler.calls == 1
