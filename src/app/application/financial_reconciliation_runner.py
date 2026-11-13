"""Execução paginada e isolada da reconciliação financeira."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.finance import FinanceStatusPort
from app.integrations.http import (
    DependencyAuthenticationError,
    DependencyAuthorizationError,
    DependencyContractError,
    DependencyTimeoutError,
    DependencyUnavailableError,
)
from app.modules.ordens_servico.application.financial_reconciliation import (
    FinancialReconciliationCategory,
)
from app.modules.ordens_servico.application.financial_reconciliation_processor import (
    FinancialReconciliationProcessor,
)
from app.modules.ordens_servico.infrastructure.db.repositories.financial_reconciliation_repo import (
    FinancialReconciliationRepo,
)


@dataclass(frozen=True, slots=True)
class FinancialReconciliationPolicy:
    batch_size: int
    concurrency: int
    timeout_seconds: float
    stale_after: timedelta
    recheck_after: timedelta
    max_batches: int


@dataclass(frozen=True, slots=True)
class FinancialReconciliationRunSummary:
    selected: int
    synchronized: int
    unavailable: int
    inconsistent: int


class FinancialReconciliationRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        finance_factory: Callable[[UUID], FinanceStatusPort],
        policy: FinancialReconciliationPolicy,
        processor: FinancialReconciliationProcessor | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._finance_factory = finance_factory
        self._policy = policy
        self._processor = processor or FinancialReconciliationProcessor()
        self._logger = logger or logging.getLogger(__name__)

    async def run_once(
        self, stop_event: asyncio.Event | None = None
    ) -> FinancialReconciliationRunSummary:
        stop = stop_event or asyncio.Event()
        now = datetime.now(UTC)
        stale_before = now - self._policy.stale_after
        recheck_before = now - self._policy.recheck_after
        cursor: tuple[datetime, UUID] | None = None
        counts = {"selected": 0, "synchronized": 0, "unavailable": 0, "inconsistent": 0}
        semaphore = asyncio.Semaphore(self._policy.concurrency)
        for _ in range(self._policy.max_batches):
            if stop.is_set():
                break
            async with self._session_factory() as session:
                candidates = await FinancialReconciliationRepo(session).list_due(
                    batch_size=self._policy.batch_size,
                    stale_before=stale_before,
                    recheck_before=recheck_before,
                    cursor=cursor,
                )
            if not candidates:
                break

            async def bounded(order_id: UUID) -> str:
                async with semaphore:
                    if stop.is_set():
                        return "stopped"
                    return await self._reconcile_one(order_id)

            results = await asyncio.gather(
                *(bounded(candidate.order_id) for candidate in candidates)
            )
            counts["selected"] += sum(result != "stopped" for result in results)
            for result in results:
                if result in counts:
                    counts[result] += 1
            last = candidates[-1]
            cursor = (last.updated_at, last.order_id)
            if len(candidates) < self._policy.batch_size:
                break
        return FinancialReconciliationRunSummary(**counts)

    async def _reconcile_one(self, order_id: UUID) -> str:
        correlation_id = uuid4()
        checked_at = datetime.now(UTC)
        try:
            snapshot = await asyncio.wait_for(
                self._finance_factory(correlation_id).obter_status(order_id),
                timeout=self._policy.timeout_seconds,
            )
            async with self._session_factory() as session, session.begin():
                comparison = await self._processor.process(
                    order_id=order_id,
                    snapshot=snapshot,
                    correlation_id=correlation_id,
                    session=session,
                    checked_at=checked_at,
                )
            result = (
                "inconsistent"
                if comparison.category is FinancialReconciliationCategory.INCONSISTENT
                else "synchronized"
            )
            self._logger.info(
                "financial_reconciliation_result order_id=%s correlation_id=%s category=%s reason=%s",
                order_id,
                correlation_id,
                comparison.category.value,
                comparison.reason,
            )
            return result
        except (
            DependencyAuthenticationError,
            DependencyAuthorizationError,
            DependencyTimeoutError,
            DependencyUnavailableError,
            TimeoutError,
        ) as exc:
            await self._record_failure(
                order_id,
                checked_at,
                FinancialReconciliationCategory.TEMPORARILY_UNAVAILABLE,
                "financial_dependency_unavailable",
                exc,
            )
            return "unavailable"
        except DependencyContractError as exc:
            await self._record_failure(
                order_id,
                checked_at,
                FinancialReconciliationCategory.INCONSISTENT,
                "invalid_authoritative_response",
                exc,
            )
            return "inconsistent"
        except Exception as exc:
            await self._record_failure(
                order_id,
                checked_at,
                FinancialReconciliationCategory.INCONSISTENT,
                "reconciliation_processing_failed",
                exc,
            )
            return "inconsistent"

    async def _record_failure(
        self,
        order_id: UUID,
        checked_at: datetime,
        status: FinancialReconciliationCategory,
        reason: str,
        exc: Exception,
    ) -> None:
        safe_error = type(exc).__name__[:512]
        async with self._session_factory() as session, session.begin():
            await FinancialReconciliationRepo(session).record(
                order_id=order_id,
                status=status,
                reason=reason,
                checked_at=checked_at,
                error=safe_error,
            )
        self._logger.warning(
            "financial_reconciliation_failed order_id=%s category=%s error_type=%s",
            order_id,
            status.value,
            safe_error,
        )
