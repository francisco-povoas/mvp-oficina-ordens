"""Processo one-shot, agendável externamente, sem depender do FastAPI."""

import asyncio
import logging
import signal
from datetime import timedelta
from uuid import UUID

import httpx

from app.application.financial_reconciliation_runner import (
    FinancialReconciliationPolicy,
    FinancialReconciliationRunner,
)
from app.config import get_settings
from app.integrations.finance import FinanceHTTPClient
from app.integrations.http import AsyncServiceTokenProvider, ServiceClientConfig
from app.shared.infra.db import dispose_engine, get_session_factory


async def run() -> None:
    settings = get_settings()
    settings.validate_reconciliation_runtime()
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("app.financial_reconciliation")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        logger.info("financial_reconciliation_shutdown_requested")
        stop_event.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signal_number, request_shutdown)
        except NotImplementedError:  # pragma: no cover
            signal.signal(signal_number, lambda *_: request_shutdown())

    token_provider = AsyncServiceTokenProvider(
        ServiceClientConfig(
            token_url=settings.internal_auth_url,
            client_id=settings.service_client_id,
            client_secret=settings.service_client_secret,
            connect_timeout_seconds=settings.internal_connect_timeout_seconds,
            read_timeout_seconds=settings.internal_read_timeout_seconds,
        )
    )
    timeout = httpx.Timeout(
        connect=settings.internal_connect_timeout_seconds,
        read=settings.internal_read_timeout_seconds,
        write=settings.internal_read_timeout_seconds,
        pool=settings.internal_connect_timeout_seconds,
    )

    def finance_factory(correlation_id: UUID) -> FinanceHTTPClient:
        return FinanceHTTPClient(
            base_url=settings.finance_base_url,
            service_name="mvp-oficina-financeiro",
            token_provider=token_provider,
            correlation_id=str(correlation_id),
            timeout=timeout,
        )

    runner = FinancialReconciliationRunner(
        session_factory=get_session_factory(),
        finance_factory=finance_factory,
        policy=FinancialReconciliationPolicy(
            batch_size=settings.reconciliation_batch_size,
            concurrency=settings.reconciliation_concurrency,
            timeout_seconds=settings.reconciliation_timeout_seconds,
            stale_after=timedelta(seconds=settings.reconciliation_stale_after_seconds),
            recheck_after=timedelta(seconds=settings.reconciliation_recheck_seconds),
            max_batches=settings.reconciliation_max_batches,
        ),
        logger=logger,
    )
    try:
        summary = await runner.run_once(stop_event)
        logger.info(
            "financial_reconciliation_completed selected=%s synchronized=%s unavailable=%s inconsistent=%s",
            summary.selected,
            summary.synchronized,
            summary.unavailable,
            summary.inconsistent,
        )
    finally:
        await dispose_engine()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
