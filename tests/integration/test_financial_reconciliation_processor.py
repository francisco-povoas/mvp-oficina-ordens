"""Reconciliação transacional contra PostgreSQL real."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.financial_reconciliation_runner import (
    FinancialReconciliationPolicy,
    FinancialReconciliationRunner,
)
from app.integrations.finance import (
    FinancialStatus,
    PaymentFinancialStatus,
    QuoteFinancialStatus,
)
from app.integrations.http import DependencyUnavailableError
from app.modules.ordens_servico.application.financial_event_handler import (
    FinancialEventHandler,
)
from app.modules.ordens_servico.application.financial_reconciliation import (
    FinancialReconciliationCategory,
)
from app.modules.ordens_servico.application.financial_reconciliation_processor import (
    FinancialReconciliationProcessor,
)
from app.modules.ordens_servico.infrastructure.db.models.financial_reconciliation_model import (
    FinancialReconciliationModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
    OrdemServicoModel,
)
from app.shared.infra.db.outbox_model import OutboxMessageModel


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_order(engine: AsyncEngine, *, status="DIAGNOSTICO_CONCLUIDO") -> UUID:
    now = datetime.now(UTC)
    customer_id, vehicle_id, order_id = uuid4(), uuid4(), uuid4()
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente (id, tipo_pessoa, nome_razao_social, cpf_cnpj, "
                "telefone, ativo, criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Reconciliação', :cpf, '71999999999', true, :now, :now)"
            ),
            params={"id": customer_id, "cpf": str(customer_id.int)[-11:], "now": now},
        )
        await session.exec(
            text(
                "INSERT INTO veiculo (id, cliente_id, placa, marca, modelo, criado_em, "
                "atualizado_em) VALUES (:id, :customer, :plate, 'Ford', 'Ka', :now, :now)"
            ),
            params={
                "id": vehicle_id,
                "customer": customer_id,
                "plate": f"R{vehicle_id.hex[:6]}",
                "now": now,
            },
        )
        await session.exec(
            text(
                "INSERT INTO ordem_servico (id, cliente_id, veiculo_id, status, "
                "queixa_inicial, diagnostico, criado_em, atualizado_em, "
                "iniciado_diagnostico_em, diagnostico_concluido_em, version) VALUES "
                "(:id, :customer, :vehicle, :status, 'Ruído', 'Correia', :now, :now, "
                ":now, :now, 1)"
            ),
            params={
                "id": order_id,
                "customer": customer_id,
                "vehicle": vehicle_id,
                "status": status,
                "now": now,
            },
        )
    return order_id


def _snapshot(order_id: UUID, *, payment_status=None) -> FinancialStatus:
    decided_at = datetime(2026, 11, 12, 12, 5, tzinfo=UTC)
    quote_id = uuid4()
    payment = None
    if payment_status:
        payment = PaymentFinancialStatus(
            payment_id=uuid4(),
            quote_id=quote_id,
            quote_version=3,
            attempt_number=1,
            payment_version=2,
            status=payment_status,
            amount=Decimal("150.00"),
            confirmed_at=decided_at if payment_status == "CONFIRMED" else None,
        )
    return FinancialStatus(
        order_id=order_id,
        quote=QuoteFinancialStatus(
            quote_id=quote_id,
            quote_version=3,
            status="APROVADO",
            total=Decimal("150.00"),
            communicated_at=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
            decided_at=decided_at,
            rejection_reason=None,
        ),
        payment=payment,
        reconciled_at=decided_at,
    )


async def _process(engine, order_id, snapshot, processor=None):
    async with _factory(engine)() as session, session.begin():
        return await (processor or FinancialReconciliationProcessor()).process(
            order_id=order_id,
            snapshot=snapshot,
            correlation_id=uuid4(),
            session=session,
        )


async def test_aprovacao_sem_comunicacao_converge_e_repeticao_nao_reaplica(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    snapshot = _snapshot(order_id)

    first = await _process(db_engine, order_id, snapshot)
    second = await _process(db_engine, order_id, snapshot)

    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        marker = await session.get(FinancialReconciliationModel, order_id)
    assert first.category is second.category is FinancialReconciliationCategory.SYNCHRONIZED
    assert order.status == "APROVADA"
    assert order.quote_status == "APROVADO"
    assert order.version == 2
    assert marker.check_count == 2
    assert marker.last_applied_at is not None


async def test_pagamento_confirmado_converge_sem_outbox_consumo_ou_entrega(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine, status="FINALIZADA")
    snapshot = _snapshot(order_id, payment_status="CONFIRMED")

    await _process(db_engine, order_id, snapshot)

    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        outbox_count = (
            await session.exec(select(func.count()).select_from(OutboxMessageModel))
        ).one()[0]
    assert order.status == "FINALIZADA"
    assert order.payment_status == "CONFIRMED"
    assert order.payment_id == snapshot.payment.payment_id
    assert order.version == 3
    assert outbox_count == 0


async def test_unknown_preserva_fail_closed_sem_projetar_pagamento(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    await _process(db_engine, order_id, _snapshot(order_id, payment_status="UNKNOWN"))
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
    assert order.quote_status == "APROVADO"
    assert order.payment_id is None
    assert order.payment_status is None


async def test_handler_e_reconciliador_concorrentes_aplicam_uma_vez(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    snapshot = _snapshot(order_id)
    event = {
        "message_id": str(uuid4()),
        "message_type": "QuoteApproved",
        "message_version": 1,
        "occurred_at": snapshot.quote.decided_at.isoformat(),
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "operation_id": str(uuid4()),
        "saga_id": None,
        "payload": {
            "order_id": str(order_id),
            "quote_id": str(snapshot.quote.quote_id),
            "quote_version": 3,
            "approved_at": snapshot.quote.decided_at.isoformat(),
        },
    }

    async def event_task():
        async with _factory(db_engine)() as session, session.begin():
            await FinancialEventHandler().handle(event, session)

    await asyncio.gather(event_task(), _process(db_engine, order_id, snapshot))
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
    assert order.quote_status == "APROVADO"
    assert order.version == 2


async def test_falha_depois_da_aplicacao_reverte_projecao_e_marcador(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    snapshot = _snapshot(order_id)

    class FailingApplier(FinancialEventHandler):
        async def apply_authoritative_snapshot(self, *args, **kwargs):
            await super().apply_authoritative_snapshot(*args, **kwargs)
            raise RuntimeError("falha injetada")

    try:
        await _process(
            db_engine,
            order_id,
            snapshot,
            FinancialReconciliationProcessor(FailingApplier()),
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("a falha injetada deveria propagar")

    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        marker = await session.get(FinancialReconciliationModel, order_id)
    assert order.quote_id is None
    assert order.version == 1
    assert marker is None


class FakeFinance:
    def __init__(self, result) -> None:
        self.result = result

    async def obter_status(self, order_id):
        if isinstance(self.result, Exception):
            raise self.result
        if callable(self.result):
            return self.result(order_id)
        return self.result


def _policy(*, batch=2, batches=10, recheck_seconds=3600):
    from datetime import timedelta

    return FinancialReconciliationPolicy(
        batch_size=batch,
        concurrency=2,
        timeout_seconds=1,
        stale_after=timedelta(0),
        recheck_after=timedelta(seconds=recheck_seconds),
        max_batches=batches,
    )


async def test_runner_pagina_limita_lote_e_reinicio_respeita_marcador(
    db_engine: AsyncEngine,
) -> None:
    order_ids = [await _seed_order(db_engine) for _ in range(5)]
    runner = FinancialReconciliationRunner(
        session_factory=_factory(db_engine),
        finance_factory=lambda _: FakeFinance(None),
        policy=_policy(batch=2, batches=2),
    )

    first = await runner.run_once()
    restarted = await FinancialReconciliationRunner(
        session_factory=_factory(db_engine),
        finance_factory=lambda _: FakeFinance(None),
        policy=_policy(batch=10, batches=1),
    ).run_once()

    assert first.selected == 4
    assert first.synchronized == 4
    assert restarted.selected == 1
    async with _factory(db_engine)() as session:
        markers = (
            await session.exec(select(FinancialReconciliationModel))
        ).all()
    assert {marker[0].order_id for marker in markers} == set(order_ids)


async def test_runner_isola_indisponibilidade_e_converge_em_nova_execucao(
    db_engine: AsyncEngine,
) -> None:
    unavailable_id = await _seed_order(db_engine)
    healthy_id = await _seed_order(db_engine)
    results = {
        unavailable_id: DependencyUnavailableError("offline"),
        healthy_id: None,
    }

    first = await FinancialReconciliationRunner(
        session_factory=_factory(db_engine),
        finance_factory=lambda _: FakeFinance(
            lambda order_id: (
                (_ for _ in ()).throw(results[order_id])
                if isinstance(results[order_id], Exception)
                else results[order_id]
            )
        ),
        policy=_policy(recheck_seconds=0),
    ).run_once()
    results[unavailable_id] = _snapshot(unavailable_id)
    second = await FinancialReconciliationRunner(
        session_factory=_factory(db_engine),
        finance_factory=lambda _: FakeFinance(lambda order_id: results[order_id]),
        policy=_policy(recheck_seconds=0),
    ).run_once()

    assert first.selected == 2
    assert first.unavailable == 1
    assert first.synchronized == 1
    assert second.selected == 2
    async with _factory(db_engine)() as session:
        recovered = await session.get(OrdemServicoModel, unavailable_id)
        marker = await session.get(FinancialReconciliationModel, unavailable_id)
    assert recovered.quote_status == "APROVADO"
    assert marker.status == "SYNCHRONIZED"
    assert marker.check_count == 2


async def test_runner_shutdown_antes_da_selecao_nao_inicia_trabalho(
    db_engine: AsyncEngine,
) -> None:
    await _seed_order(db_engine)
    stop = asyncio.Event()
    stop.set()
    summary = await FinancialReconciliationRunner(
        session_factory=_factory(db_engine),
        finance_factory=lambda _: FakeFinance(None),
        policy=_policy(),
    ).run_once(stop)
    assert summary.selected == 0
