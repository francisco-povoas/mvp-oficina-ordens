import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.application.messaging import (
    MessageContractError,
    MessagePublicationError,
    MessagePublicationIndeterminateError,
)
from app.application.outbox_publisher import (
    OutboxPublisher,
    PublisherPolicy,
    StaticOrdensMessageRouter,
    build_envelope,
)
from app.infrastructure.messaging.contracts import JsonSchemaMessageValidator
from app.infrastructure.messaging.sqs import SqsFifoTransport
from app.shared.outbox import ClaimedOutboxMessage

NOW = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)


def claimed_message(
    *,
    event_type: str = "ReserveItem.v1",
    attempts: int = 1,
    saga_id=None,
    payload: dict | None = None,
) -> ClaimedOutboxMessage:
    operation_id = uuid4()
    return ClaimedOutboxMessage(
        message_id=uuid4(),
        aggregate_type="StockOperation",
        aggregate_id=operation_id,
        event_type=event_type,
        event_version=1,
        payload=payload
        or {
            "order_id": str(uuid4()),
            "order_item_id": str(uuid4()),
            "resource_id": str(uuid4()),
            "quantity": 2,
            "resource_snapshot": {
                "name": "Filtro",
                "description": None,
                "unit_price": "42.90",
            },
        },
        correlation_id=uuid4(),
        causation_id=None,
        saga_id=saga_id or uuid4(),
        occurred_at=NOW,
        attempts=attempts,
        claim_id=uuid4(),
        claimed_by="worker-test",
        claimed_at=NOW,
        claim_expires_at=NOW + timedelta(seconds=30),
    )


class FakeStore:
    def __init__(self, messages: list[ClaimedOutboxMessage]) -> None:
        self.messages = messages
        self.claim_args = None
        self.published: list[dict] = []
        self.failed: list[dict] = []
        self.confirm_error: Exception | None = None

    async def claim_pending(self, **kwargs):
        self.claim_args = kwargs
        messages, self.messages = self.messages, []
        return messages

    async def mark_published(self, **kwargs):
        if self.confirm_error:
            raise self.confirm_error
        self.published.append(kwargs)

    async def mark_failed(self, **kwargs):
        self.failed.append(kwargs)


class FakeTransport:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[dict] = []

    async def publish(self, **kwargs):
        if self.error:
            raise self.error
        self.sent.append(kwargs)
        return "elasticmq-message-id"

    async def close(self):
        return None


class PassValidator:
    def validate(self, envelope):
        return None


def publisher(store: FakeStore, transport: FakeTransport, **kwargs) -> OutboxPublisher:
    return OutboxPublisher(
        worker_id="worker-test",
        store=store,
        router=StaticOrdensMessageRouter("http://elasticmq/queue.fifo"),
        validator=kwargs.get("validator", PassValidator()),
        transport=transport,
        policy=kwargs.get(
            "policy",
            PublisherPolicy(
                batch_size=10,
                lease_seconds=30,
                poll_interval_seconds=0.01,
                max_attempts=8,
                initial_backoff_seconds=1,
                max_backoff_seconds=60,
            ),
        ),
        clock=lambda: NOW,
        jitter=lambda _start, cap: cap,
    )


async def test_publica_envelope_fifo_e_confirma_somente_apos_transporte() -> None:
    message = claimed_message()
    store = FakeStore([message])
    transport = FakeTransport()

    assert await publisher(store, transport).run_once() == 1

    assert store.claim_args["max_attempts"] == 8
    assert len(transport.sent) == 1
    sent = transport.sent[0]
    body = json.loads(sent["body"])
    assert body["message_id"] == str(message.message_id)
    assert body["correlation_id"] == str(message.correlation_id)
    assert body["operation_id"] == str(message.aggregate_id)
    assert body["saga_id"] == str(message.saga_id)
    assert sent["deduplication_id"] == str(message.message_id)
    assert sent["route"].group_id == str(message.aggregate_id)
    assert store.published[0]["message_id"] == message.message_id
    assert store.failed == []


async def test_valida_schema_real_antes_de_publicar() -> None:
    invalid = claimed_message(payload={"quantity": 0})
    store = FakeStore([invalid])
    transport = FakeTransport()
    validator = JsonSchemaMessageValidator("contracts/messages/saga-stock.v1.schema.json")

    await publisher(store, transport, validator=validator).run_once()

    assert transport.sent == []
    assert store.published == []
    assert store.failed[0]["error"].startswith("MessageContractError:")


async def test_mensagem_sem_rota_nao_e_marcada_como_publicada() -> None:
    message = claimed_message(event_type="ResourceReserved.v1")
    store = FakeStore([message])
    transport = FakeTransport()

    await publisher(store, transport).run_once()

    assert transport.sent == []
    assert store.published == []
    assert store.failed[0]["error"].startswith("UnknownMessageRouteError:")


async def test_falha_de_transporte_agenda_backoff_limitado() -> None:
    message = claimed_message(attempts=4)
    store = FakeStore([message])
    transport = FakeTransport(MessagePublicationError("endpoint indisponivel"))

    await publisher(store, transport).run_once()

    assert store.published == []
    failure = store.failed[0]
    assert failure["failed_at"] == NOW
    assert failure["next_attempt_at"] == NOW + timedelta(seconds=8)
    assert "endpoint indisponivel" in failure["error"]


async def test_falha_de_confirmacao_nao_reclassifica_envio_positivo() -> None:
    store = FakeStore([claimed_message()])
    store.confirm_error = RuntimeError("postgres indisponivel")
    transport = FakeTransport()

    await publisher(store, transport).run_once()

    assert len(transport.sent) == 1
    assert store.published == []
    assert store.failed == []


async def test_loop_encerra_por_evento_sem_polling_agressivo() -> None:
    store = FakeStore([])
    transport = FakeTransport()
    stop = asyncio.Event()
    task = asyncio.create_task(publisher(store, transport).run(stop))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=0.2)
    assert store.claim_args is not None


def test_envelope_rejeita_tipo_e_versao_incoerentes() -> None:
    with pytest.raises(MessageContractError):
        build_envelope(claimed_message(event_type="ReserveItem.v2"))


class SlowClient:
    def send_message(self, **_kwargs):
        time.sleep(0.05)
        return {"MessageId": "late", "ResponseMetadata": {"HTTPStatusCode": 200}}

    def close(self):
        return None


class UnknownResultClient:
    def send_message(self, **_kwargs):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def close(self):
        return None


async def test_timeout_sqs_e_resultado_indeterminado_sao_tipados() -> None:
    route = StaticOrdensMessageRouter("queue-url").resolve(claimed_message())
    with pytest.raises(MessagePublicationIndeterminateError, match="timeout"):
        await SqsFifoTransport(SlowClient(), send_timeout_seconds=0.001).publish(
            route=route,
            body="{}",
            deduplication_id=str(uuid4()),
        )
    with pytest.raises(MessagePublicationIndeterminateError, match="confirmacao"):
        await SqsFifoTransport(UnknownResultClient(), send_timeout_seconds=1).publish(
            route=route,
            body="{}",
            deduplication_id=str(uuid4()),
        )
