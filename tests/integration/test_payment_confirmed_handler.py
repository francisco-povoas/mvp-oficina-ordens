"""Pagamento confirmado atualiza só a projeção financeira da OS."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.inbox_consumer import ProcessingOutcome
from app.consumer import FINANCIAL_CONSUMER
from app.infrastructure.messaging.inbox import InboxMessageModel, PostgresInboxProcessor
from app.modules.ordens_servico.application.financial_event_handler import (
    FinancialEventHandler,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoTransicaoInvalidaError,
)
from app.modules.ordens_servico.infrastructure.db.models.distributed_operation_model import (
    DistributedOperationModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
    OrdemServicoModel,
)
from app.shared.infra.db.outbox_model import OutboxMessageModel


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_order(
    engine: AsyncEngine,
    *,
    status: str = "FINALIZADA",
    quote_id: UUID | None = None,
) -> UUID:
    now = datetime.now(UTC)
    customer_id, vehicle_id, order_id = uuid4(), uuid4(), uuid4()
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente "
                "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
                "criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Pagamento', :cpf, '71999999999', true, :now, :now)"
            ),
            params={"id": customer_id, "cpf": str(customer_id.int)[-11:], "now": now},
        )
        await session.exec(
            text(
                "INSERT INTO veiculo "
                "(id, cliente_id, placa, marca, modelo, criado_em, atualizado_em) "
                "VALUES (:id, :customer_id, :plate, 'Ford', 'Ka', :now, :now)"
            ),
            params={
                "id": vehicle_id,
                "customer_id": customer_id,
                "plate": f"P{str(vehicle_id).replace('-', '')[:6]}",
                "now": now,
            },
        )
        await session.exec(
            text(
                "INSERT INTO ordem_servico "
                "(id, cliente_id, veiculo_id, status, queixa_inicial, diagnostico, "
                "criado_em, atualizado_em, iniciado_diagnostico_em, "
                "diagnostico_concluido_em, version, quote_id, quote_status, "
                "quote_version, quote_total) VALUES "
                "(:id, :customer_id, :vehicle_id, :status, 'Ruído', 'Correia', "
                ":now, :now, :now, :now, 1, :quote_id, :quote_status, "
                ":quote_version, :quote_total)"
            ),
            params={
                "id": order_id,
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "status": status,
                "quote_id": quote_id,
                "quote_status": "APROVADO" if quote_id else None,
                "quote_version": 3 if quote_id else None,
                "quote_total": "150.00" if quote_id else None,
                "now": now,
            },
        )
    return order_id


def _event(
    order_id: UUID,
    quote_id: UUID,
    *,
    operation_id: UUID | None = None,
    payment_id: UUID | None = None,
) -> dict[str, object]:
    return {
        "message_id": str(uuid4()),
        "message_type": "PaymentConfirmed",
        "message_version": 1,
        "occurred_at": "2026-11-12T14:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "operation_id": str(operation_id or uuid4()),
        "saga_id": None,
        "payload": {
            "order_id": str(order_id),
            "quote_id": str(quote_id),
            "quote_version": 3,
            "payment_id": str(payment_id or uuid4()),
            "status": "CONFIRMED",
            "amount": "150.00",
            "confirmed_at": "2026-11-12T14:00:00Z",
        },
    }


def _processor(
    engine: AsyncEngine, handler: FinancialEventHandler | None = None
) -> PostgresInboxProcessor:
    return PostgresInboxProcessor(
        _factory(engine),
        consumer_name=FINANCIAL_CONSUMER,
        handler=handler or FinancialEventHandler(),
    )


async def _order(engine: AsyncEngine, order_id: UUID) -> OrdemServicoModel:
    async with _factory(engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        assert order is not None
        return order


async def test_pagamento_atualiza_projecao_sem_consumir_ou_entregar(
    db_engine: AsyncEngine,
) -> None:
    quote_id, payment_id = uuid4(), uuid4()
    order_id = await _seed_order(db_engine, quote_id=quote_id)
    event = _event(order_id, quote_id, payment_id=payment_id)

    assert (
        await _processor(db_engine).process(event, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    projected = await _order(db_engine, order_id)
    async with _factory(db_engine)() as session:
        operations = await session.scalar(
            select(func.count()).select_from(DistributedOperationModel)
        )
        outbox = await session.scalar(select(func.count()).select_from(OutboxMessageModel))
    assert projected.status == "FINALIZADA"
    assert projected.payment_id == payment_id
    assert projected.payment_status == "CONFIRMED"
    assert projected.payment_quote_id == quote_id
    assert projected.payment_quote_version == 3
    assert operations == outbox == 0


async def test_nova_emissao_equivalente_e_deduplicada_sem_nova_versao(
    db_engine: AsyncEngine,
) -> None:
    quote_id, operation_id, payment_id = uuid4(), uuid4(), uuid4()
    order_id = await _seed_order(db_engine, quote_id=quote_id)
    event = _event(
        order_id,
        quote_id,
        operation_id=operation_id,
        payment_id=payment_id,
    )
    duplicate = dict(event, message_id=str(uuid4()))

    await _processor(db_engine).process(event, receive_count=1)
    first = await _order(db_engine, order_id)
    assert (
        await _processor(db_engine).process(duplicate, receive_count=2)
        is ProcessingOutcome.DUPLICATE
    )
    assert (await _order(db_engine, order_id)).version == first.version


async def test_sem_projecao_de_orcamento_pagamento_reconstroi_aprovacao_minima(
    db_engine: AsyncEngine,
) -> None:
    quote_id = uuid4()
    order_id = await _seed_order(db_engine)

    await _processor(db_engine).process(_event(order_id, quote_id), receive_count=1)
    projected = await _order(db_engine, order_id)
    assert projected.quote_id == quote_id
    assert projected.quote_status == "APROVADO"
    assert projected.quote_version == 3


async def test_orcamento_divergente_reverte_inbox_e_projecao(
    db_engine: AsyncEngine,
) -> None:
    quote_id = uuid4()
    order_id = await _seed_order(db_engine, quote_id=quote_id)
    inconsistent = _event(order_id, uuid4())

    with pytest.raises(OrdemServicoInvalidaError):
        await _processor(db_engine).process(inconsistent, receive_count=1)
    projected = await _order(db_engine, order_id)
    async with _factory(db_engine)() as session:
        inbox = await session.scalar(
            select(func.count())
            .select_from(InboxMessageModel)
            .where(InboxMessageModel.message_id == UUID(str(inconsistent["message_id"])))
        )
    assert projected.payment_id is None
    assert inbox == 0


async def test_pagamento_em_estado_incompativel_falha_sem_confirmacao(
    db_engine: AsyncEngine,
) -> None:
    quote_id = uuid4()
    order_id = await _seed_order(db_engine, status="APROVADA", quote_id=quote_id)

    with pytest.raises(OrdemServicoTransicaoInvalidaError):
        await _processor(db_engine).process(
            _event(order_id, quote_id),
            receive_count=1,
        )
    assert (await _order(db_engine, order_id)).payment_id is None


class FailingPaymentHandler(FinancialEventHandler):
    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        if message_type == "PaymentConfirmed":
            raise RuntimeError("falha após projetar pagamento")


async def test_falha_apos_projecao_reverte_pagamento_e_inbox(
    db_engine: AsyncEngine,
) -> None:
    quote_id = uuid4()
    order_id = await _seed_order(db_engine, quote_id=quote_id)
    event = _event(order_id, quote_id)

    with pytest.raises(RuntimeError, match="após projetar"):
        await _processor(db_engine, FailingPaymentHandler()).process(event, receive_count=1)
    assert (await _order(db_engine, order_id)).payment_id is None
