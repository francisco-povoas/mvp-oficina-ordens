"""Recusa financeira reutiliza liberações e só encerra após Recursos."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.inbox_consumer import ProcessingOutcome
from app.consumer import FINANCIAL_CONSUMER, RESOURCE_CONSUMER
from app.infrastructure.messaging.inbox import PostgresInboxProcessor
from app.modules.ordens_servico.application.financial_event_handler import (
    FinancialEventHandler,
)
from app.modules.ordens_servico.application.resource_result_handler import (
    ResourceResultHandler,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import ReservaItemSaga
from app.modules.ordens_servico.infrastructure.db.models.distributed_operation_model import (
    DistributedOperationModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_item_model import (
    OrdemServicoItemModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
    OrdemServicoModel,
)
from app.modules.ordens_servico.infrastructure.db.models.reserva_item_saga_model import (
    ReservaItemSagaModel,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.shared.infra.db.outbox_model import OutboxMessageModel


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(
    engine: AsyncEngine,
    *,
    item_statuses: tuple[str, ...] = (),
    pending_saga: bool = False,
) -> tuple[UUID, list[UUID], ReservaItemSaga | None]:
    now = datetime.now(UTC)
    customer_id, vehicle_id, order_id = uuid4(), uuid4(), uuid4()
    item_ids: list[UUID] = []
    pending: ReservaItemSaga | None = None
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente "
                "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
                "criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Recusa', :cpf, '71999999999', true, :now, :now)"
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
                "plate": f"R{str(vehicle_id).replace('-', '')[:6]}",
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
        for status in item_statuses:
            item_id, resource_id = uuid4(), uuid4()
            item_ids.append(item_id)
            await session.exec(
                text(
                    "INSERT INTO ordem_servico_item "
                    "(id, ordem_servico_id, item_estoque_id, nome_item, tipo_item, "
                    "quantidade, valor_unitario, status) VALUES "
                    "(:id, :order_id, :resource_id, 'Correia', 'PECA', 1, 50.00, :status)"
                ),
                params={
                    "id": item_id,
                    "order_id": order_id,
                    "resource_id": resource_id,
                    "status": status,
                },
            )
            if status == "RESERVADO":
                saga = ReservaItemSaga.iniciar(
                    saga_id=uuid4(),
                    operation_id=uuid4(),
                    ordem_servico_id=order_id,
                    ordem_servico_item_id=item_id,
                    resource_id=resource_id,
                    quantity=1,
                    correlation_id=uuid4(),
                    idempotency_key=f"reject-{item_id}",
                    request_hash="a" * 64,
                    reserve_message_id=uuid4(),
                    agora=now,
                ).registrar_reserva(
                    reservation_id=uuid4(),
                    message_id=uuid4(),
                    resultado_em=now,
                    resource_name="Correia",
                    resource_description=None,
                    resource_unit_price=Decimal("50.00"),
                )
                await ReservaItemSagaRepo(session).salvar(saga)
        if pending_saga:
            pending = ReservaItemSaga.iniciar(
                saga_id=uuid4(),
                operation_id=uuid4(),
                ordem_servico_id=order_id,
                ordem_servico_item_id=uuid4(),
                resource_id=uuid4(),
                quantity=1,
                correlation_id=uuid4(),
                idempotency_key=f"pending-{order_id}",
                request_hash="b" * 64,
                reserve_message_id=uuid4(),
                agora=now,
            )
            await ReservaItemSagaRepo(session).salvar(pending)
    return order_id, item_ids, pending


def _financial_event(message_type: str, order_id: UUID, quote_id: UUID) -> dict[str, object]:
    payload: dict[str, object] = {
        "order_id": str(order_id),
        "quote_id": str(quote_id),
        "quote_version": 2 if message_type == "QuoteCommunicated" else 3,
    }
    if message_type == "QuoteCommunicated":
        payload.update(total="50.00", communicated_at="2026-11-12T13:00:00Z")
    else:
        payload.update(
            reason="Cliente recusou o orçamento",
            rejected_at="2026-11-12T13:05:00Z",
        )
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": "2026-11-12T13:05:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": None,
        "operation_id": str(uuid4()),
        "saga_id": None,
        "payload": payload,
    }


def _processor(
    engine: AsyncEngine,
    *,
    consumer: str = FINANCIAL_CONSUMER,
    handler=None,
) -> PostgresInboxProcessor:
    return PostgresInboxProcessor(
        _factory(engine),
        consumer_name=consumer,
        handler=handler or FinancialEventHandler(),
    )


async def _communicate_and_reject(
    engine: AsyncEngine,
    order_id: UUID,
    *,
    handler=None,
) -> tuple[UUID, dict[str, object]]:
    quote_id = uuid4()
    await _processor(engine).process(
        _financial_event("QuoteCommunicated", order_id, quote_id),
        receive_count=1,
    )
    rejected = _financial_event("QuoteRejected", order_id, quote_id)
    await _processor(engine, handler=handler).process(rejected, receive_count=1)
    return quote_id, rejected


async def test_backorder_cancela_sem_liberacao_e_encerra(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids, _ = await _seed(db_engine, item_statuses=("A_RECEBER",))
    quote_id, _ = await _communicate_and_reject(db_engine, order_id)

    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        item = await session.get(OrdemServicoItemModel, item_ids[0])
        operations = list((await session.exec(select(DistributedOperationModel))).all())
        release_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.event_type == "ReleaseReservation.v1")
        )
    assert order is not None and order.status == "ENCERRADA"
    assert order.quote_id == quote_id and order.quote_status == "RECUSADO"
    assert order.quote_rejection_reason == "Cliente recusou o orçamento"
    assert item is not None and item.status == "CANCELADO"
    assert len(operations) == 1 and operations[0].terminal_result == "NO_RESERVATION"
    assert release_count == 0


async def test_reserva_so_encerra_apos_confirmacao_de_recursos(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids, _ = await _seed(db_engine, item_statuses=("RESERVADO",))
    _, rejected = await _communicate_and_reject(db_engine, order_id)

    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        operation = (await session.exec(select(DistributedOperationModel))).one()
        command = (
            await session.exec(
                select(OutboxMessageModel).where(
                    OutboxMessageModel.aggregate_id == operation.operation_id
                )
            )
        ).one()
    assert order is not None and order.status == "AGUARDANDO_APROVACAO"
    assert operation.state == "PENDING"
    assert command.causation_id == UUID(str(rejected["message_id"]))
    assert json.loads(command.payload)["reason"] == "QUOTE_REJECTED"

    result = {
        "message_id": str(uuid4()),
        "message_type": "ReservationReleased",
        "message_version": 1,
        "occurred_at": "2026-11-12T13:10:00Z",
        "correlation_id": str(operation.correlation_id),
        "causation_id": str(operation.command_message_id),
        "operation_id": str(operation.operation_id),
        "saga_id": None,
        "payload": {
            "reservation_id": str(operation.reservation_id),
            "resource_id": str(operation.resource_id),
            "quantity": operation.quantity,
            "released_at": "2026-11-12T13:10:00Z",
        },
    }
    assert (
        await _processor(
            db_engine,
            consumer=RESOURCE_CONSUMER,
            handler=ResourceResultHandler(),
        ).process(result, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        item = await session.get(OrdemServicoItemModel, item_ids[0])
    assert order is not None and order.status == "ENCERRADA"
    assert item is not None and item.status == "CANCELADO"


async def test_saga_pendente_e_cancelada_e_resultado_tardio_fecha_os(
    db_engine: AsyncEngine,
) -> None:
    order_id, _, pending = await _seed(db_engine, pending_saga=True)
    assert pending is not None
    await _communicate_and_reject(db_engine, order_id)
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        stored = await session.get(ReservaItemSagaModel, pending.id)
    assert order is not None and order.status == "AGUARDANDO_APROVACAO"
    assert stored is not None and stored.estado == "CANCEL_REQUESTED"

    result = {
        "message_id": str(uuid4()),
        "message_type": "ItemBackordered",
        "message_version": 1,
        "occurred_at": "2026-11-12T13:10:00Z",
        "correlation_id": str(pending.correlation_id),
        "causation_id": str(pending.ultimo_comando_message_id),
        "operation_id": str(pending.operation_id),
        "saga_id": str(pending.id),
        "payload": {
            "resource_id": str(pending.resource_id),
            "requested_quantity": 1,
            "available_quantity": 0,
            "decided_at": "2026-11-12T13:10:00Z",
            "resource_snapshot": {
                "name": "Correia",
                "description": None,
                "unit_price": "50.00",
            },
        },
    }
    await _processor(
        db_engine,
        consumer=RESOURCE_CONSUMER,
        handler=ResourceResultHandler(),
    ).process(result, receive_count=1)
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        stored = await session.get(ReservaItemSagaModel, pending.id)
        item = await session.get(OrdemServicoItemModel, pending.ordem_servico_item_id)
    assert order is not None and order.status == "ENCERRADA"
    assert stored is not None and stored.terminal_result == "NO_RESERVATION"
    assert item is not None and item.status == "CANCELADO"


class FailingRejectionHandler(FinancialEventHandler):
    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        if message_type == "QuoteRejected":
            raise RuntimeError("falha antes do commit financeiro")


async def test_falha_reverte_recusa_operacao_outbox_e_item(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids, _ = await _seed(db_engine, item_statuses=("RESERVADO",))
    quote_id = uuid4()
    await _processor(db_engine).process(
        _financial_event("QuoteCommunicated", order_id, quote_id),
        receive_count=1,
    )
    rejected = _financial_event("QuoteRejected", order_id, quote_id)
    with pytest.raises(RuntimeError, match="antes do commit"):
        await _processor(db_engine, handler=FailingRejectionHandler()).process(
            rejected, receive_count=1
        )

    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        item = await session.get(OrdemServicoItemModel, item_ids[0])
        operation_count = await session.scalar(
            select(func.count()).select_from(DistributedOperationModel)
        )
        release_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.event_type == "ReleaseReservation.v1")
        )
    assert order is not None and order.quote_status == "COMUNICADO"
    assert order.status == "AGUARDANDO_APROVACAO"
    assert item is not None and item.status == "RESERVADO"
    assert operation_count == release_count == 0
