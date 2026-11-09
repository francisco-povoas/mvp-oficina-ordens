"""Composition root dos consumers independentes de Ordens."""

import asyncio
import logging
import signal
from dataclasses import dataclass

from app.application.inbox_consumer import ConsumerPolicy, InboxConsumer
from app.config import Settings, get_settings
from app.infrastructure.messaging.contracts import JsonSchemaMessageValidator
from app.infrastructure.messaging.inbox import PostgresInboxProcessor, TransactionalMessageHandler
from app.infrastructure.messaging.sqs import create_sqs_client
from app.infrastructure.messaging.sqs_consumer import SqsConsumerTransport
from app.shared.infra.db import dispose_engine, get_session_factory

RESOURCE_CONSUMER = "ordens.resource-results.v1"
FINANCIAL_CONSUMER = "ordens.financial-events.v1"
RESOURCE_TYPES = frozenset(
    {
        "ResourceReserved",
        "ItemBackordered",
        "ReservationRejected",
        "ReservationReleased",
        "BackorderedItemReceived",
        "ReservationConsumed",
    }
)
FINANCIAL_TYPES = frozenset(
    {"QuoteCommunicated", "QuoteApproved", "QuoteRejected", "PaymentConfirmed"}
)


@dataclass(frozen=True, slots=True)
class ConsumerHandlers:
    resource_results: TransactionalMessageHandler | None = None
    financial_events: TransactionalMessageHandler | None = None


async def run_consumers(
    settings: Settings | None = None,
    *,
    handlers: ConsumerHandlers | None = None,
) -> None:
    resolved = settings or get_settings()
    resolved.validate_consumer_runtime()
    logging.basicConfig(
        level=getattr(logging, resolved.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("app.consumer")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        logger.info("inbox_consumers_shutdown_requested")
        stop_event.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_number, request_shutdown)
        except NotImplementedError:  # pragma: no cover
            signal.signal(signal_number, lambda *_: request_shutdown())

    resolved_handlers = handlers or ConsumerHandlers()
    if not resolved_handlers.resource_results and not resolved_handlers.financial_events:
        logger.warning("inbox_consumers_paused_business_handlers_not_installed")
        await stop_event.wait()
        await dispose_engine()
        return

    client = create_sqs_client(
        region=resolved.aws_region,
        endpoint_url=resolved.sqs_endpoint_url,
        connect_timeout_seconds=resolved.sqs_connect_timeout_seconds,
        read_timeout_seconds=resolved.sqs_read_timeout_seconds,
    )
    session_factory = get_session_factory()
    policy = ConsumerPolicy(
        batch_size=resolved.consumer_batch_size,
        concurrency=resolved.consumer_concurrency,
        wait_time_seconds=resolved.consumer_wait_time_seconds,
        visibility_timeout_seconds=resolved.consumer_visibility_timeout_seconds,
        heartbeat_seconds=resolved.consumer_heartbeat_seconds,
    )
    consumers = []
    if resolved_handlers.resource_results:
        consumers.append(
            InboxConsumer(
                consumer_name=RESOURCE_CONSUMER,
                allowed_message_types=RESOURCE_TYPES,
                validator=JsonSchemaMessageValidator(resolved.resource_consumer_schema_path),
                processor=PostgresInboxProcessor(
                    session_factory,
                    consumer_name=RESOURCE_CONSUMER,
                    handler=resolved_handlers.resource_results,
                ),
                transport=SqsConsumerTransport(
                    client, queue_url=resolved.sqs_recursos_to_ordens_url
                ),
                policy=policy,
                logger=logger,
            )
        )
    if resolved_handlers.financial_events:
        consumers.append(
            InboxConsumer(
                consumer_name=FINANCIAL_CONSUMER,
                allowed_message_types=FINANCIAL_TYPES,
                validator=JsonSchemaMessageValidator(resolved.financial_consumer_schema_path),
                processor=PostgresInboxProcessor(
                    session_factory,
                    consumer_name=FINANCIAL_CONSUMER,
                    handler=resolved_handlers.financial_events,
                ),
                transport=SqsConsumerTransport(
                    client, queue_url=resolved.sqs_financeiro_to_ordens_url
                ),
                policy=policy,
                logger=logger,
            )
        )
    try:
        await asyncio.gather(*(consumer.run(stop_event) for consumer in consumers))
    finally:
        await asyncio.to_thread(client.close)
        await dispose_engine()


def main() -> None:
    asyncio.run(run_consumers())


if __name__ == "__main__":
    main()
