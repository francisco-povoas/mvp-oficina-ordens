"""Reconciliação transacional contra PostgreSQL real."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.integrations.finance import (
    FinancialStatus,
    PaymentFinancialStatus,
    QuoteFinancialStatus,
)
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
