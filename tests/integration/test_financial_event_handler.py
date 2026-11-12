"""Projeção transacional dos fatos financeiros em PostgreSQL real."""

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
from app.modules.ordens_servico.domain.exceptions import OrdemServicoInvalidaError
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
    OrdemServicoModel,
)


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_order(engine: AsyncEngine, *, backordered: bool = False) -> UUID:
    now = datetime.now(UTC)
    customer_id = uuid4()
    vehicle_id = uuid4()
    order_id = uuid4()
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente "
                "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
                "criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Financeiro', :cpf, '71999999999', true, :now, :now)"
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
                "plate": f"F{str(vehicle_id).replace('-', '')[:6]}",
                "now": now,
            },
        )
        await session.exec(
            text(
                "INSERT INTO ordem_servico "
                "(id, cliente_id, veiculo_id, status, queixa_inicial, diagnostico, "
                "criado_em, atualizado_em, iniciado_diagnostico_em, "
                "diagnostico_concluido_em, version) VALUES "
                "(:id, :customer_id, :vehicle_id, 'DIAGNOSTICO_CONCLUIDO', "
                "'Ruído', 'Correia', :now, :now, :now, :now, 1)"
            ),
            params={
                "id": order_id,
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "now": now,
            },
        )
        if backordered:
            await session.exec(
                text(
                    "INSERT INTO ordem_servico_item "
                    "(id, ordem_servico_id, item_estoque_id, nome_item, tipo_item, "
                    "quantidade, valor_unitario, status) VALUES "
                    "(:id, :order_id, :resource_id, 'Correia', 'PECA', 1, 50.00, "
                    "'A_RECEBER')"
                ),
                params={"id": uuid4(), "order_id": order_id, "resource_id": uuid4()},
            )
    return order_id


def _event(
    message_type: str,
    *,
    order_id: UUID,
    quote_id: UUID,
    operation_id: UUID | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": str(order_id),
        "quote_id": str(quote_id),
        "quote_version": 2 if message_type == "QuoteCommunicated" else 3,
    }
    if message_type == "QuoteCommunicated":
        payload.update(total="150.00", communicated_at="2026-11-12T12:00:00Z")
    else:
        payload["approved_at"] = "2026-11-12T12:05:00Z"
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": "2026-11-12T12:05:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "operation_id": str(operation_id or uuid4()),
        "saga_id": None,
        "payload": payload,
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


async def test_comunicacao_e_aprovacao_sem_pendencias_avancam_os(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    quote_id = uuid4()
    communicated = _event("QuoteCommunicated", order_id=order_id, quote_id=quote_id)
    approved = _event("QuoteApproved", order_id=order_id, quote_id=quote_id)

    assert (
        await _processor(db_engine).process(communicated, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    projected = await _order(db_engine, order_id)
    assert projected.status == "AGUARDANDO_APROVACAO"
    assert projected.quote_id == quote_id
    assert projected.quote_status == "COMUNICADO"
    assert projected.quote_version == 2
    assert str(projected.quote_total) == "150.00"

    assert (
        await _processor(db_engine).process(approved, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    projected = await _order(db_engine, order_id)
    assert projected.status == "APROVADA"
    assert projected.quote_status == "APROVADO"
    assert projected.quote_version == 3
    assert projected.version == 3


async def test_aprovacao_preserva_espera_quando_existe_backorder(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine, backordered=True)
    quote_id = uuid4()
    await _processor(db_engine).process(
        _event("QuoteCommunicated", order_id=order_id, quote_id=quote_id),
        receive_count=1,
    )

    await _processor(db_engine).process(
        _event("QuoteApproved", order_id=order_id, quote_id=quote_id),
        receive_count=1,
    )
    projected = await _order(db_engine, order_id)
    assert projected.status == "AGUARDANDO_ITENS"
    assert projected.quote_status == "APROVADO"


async def test_aprovacao_adiantada_nao_regride_quando_comunicacao_atrasada_chega(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    quote_id = uuid4()
    approved = _event("QuoteApproved", order_id=order_id, quote_id=quote_id)
    communicated = _event("QuoteCommunicated", order_id=order_id, quote_id=quote_id)

    await _processor(db_engine).process(approved, receive_count=1)
    after_approval = await _order(db_engine, order_id)
    await _processor(db_engine).process(communicated, receive_count=1)
    after_late_event = await _order(db_engine, order_id)

    assert after_approval.status == after_late_event.status == "APROVADA"
    assert after_late_event.quote_status == "APROVADO"
    assert after_late_event.quote_version == 3
    assert after_late_event.version == after_approval.version


async def test_inbox_deduplica_nova_emissao_sem_reaplicar_aprovacao(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    quote_id = uuid4()
    operation_id = uuid4()
    approved = _event(
        "QuoteApproved",
        order_id=order_id,
        quote_id=quote_id,
        operation_id=operation_id,
    )
    duplicate = dict(approved, message_id=str(uuid4()))

    assert (
        await _processor(db_engine).process(approved, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    assert (
        await _processor(db_engine).process(duplicate, receive_count=2)
        is ProcessingOutcome.DUPLICATE
    )
    projected = await _order(db_engine, order_id)
    assert projected.version == 2


async def test_orcamento_divergente_falha_sem_inbox_ou_estado_parcial(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    original_quote = uuid4()
    await _processor(db_engine).process(
        _event("QuoteCommunicated", order_id=order_id, quote_id=original_quote),
        receive_count=1,
    )
    before = await _order(db_engine, order_id)
    inconsistent = _event("QuoteApproved", order_id=order_id, quote_id=uuid4())

    with pytest.raises(OrdemServicoInvalidaError):
        await _processor(db_engine).process(inconsistent, receive_count=1)
    after = await _order(db_engine, order_id)
    async with _factory(db_engine)() as session:
        inbox_count = await session.scalar(
            select(func.count())
            .select_from(InboxMessageModel)
            .where(InboxMessageModel.message_id == UUID(str(inconsistent["message_id"])))
        )
    assert after.status == before.status
    assert after.quote_id == original_quote
    assert after.version == before.version
    assert inbox_count == 0


class FailingHandler(FinancialEventHandler):
    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        raise RuntimeError("falha injetada após os efeitos")


async def test_falha_apos_efeitos_reverte_inbox_e_projecao(
    db_engine: AsyncEngine,
) -> None:
    order_id = await _seed_order(db_engine)
    event = _event("QuoteApproved", order_id=order_id, quote_id=uuid4())

    with pytest.raises(RuntimeError, match="falha injetada"):
        await _processor(db_engine, FailingHandler()).process(event, receive_count=1)
    projected = await _order(db_engine, order_id)
    assert projected.status == "DIAGNOSTICO_CONCLUIDO"
    assert projected.quote_id is None

    assert (
        await _processor(db_engine).process(event, receive_count=2)
        is ProcessingOutcome.PROCESSED
    )
