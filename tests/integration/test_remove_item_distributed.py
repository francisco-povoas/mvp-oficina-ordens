"""Remoção de item coordenada por Outbox/Inbox em PostgreSQL real."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.inbox_consumer import ProcessingOutcome
from app.consumer import RESOURCE_CONSUMER
from app.infrastructure.messaging.inbox import PostgresInboxProcessor
from app.modules.ordens_servico.application.distributed_stock_operation_result_handler import (
    DistributedStockOperationResultHandler,
)
from app.modules.ordens_servico.application.reservation_saga_result_handler import (
    ReservationSagaResultHandler,
)
from app.modules.ordens_servico.application.use_cases.remover_item_da_ordem_servico import (
    RemoverItemDaOrdemServicoUseCase,
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
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.shared.infra.db.outbox_model import OutboxMessageModel


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_saga(engine: AsyncEngine) -> ReservaItemSaga:
    now = datetime.now(UTC)
    customer_id = uuid4()
    vehicle_id = uuid4()
    order_id = uuid4()
    saga = ReservaItemSaga.iniciar(
        saga_id=uuid4(),
        operation_id=uuid4(),
        ordem_servico_id=order_id,
        ordem_servico_item_id=uuid4(),
        resource_id=uuid4(),
        quantity=2,
        correlation_id=uuid4(),
        idempotency_key=f"remove-{uuid4()}",
        request_hash="a" * 64,
        reserve_message_id=uuid4(),
        agora=now,
    )
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente "
                "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
                "criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Remoção', :cpf, '71999999999', true, :now, :now)"
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
                "plate": f"L{str(vehicle_id).replace('-', '')[:6]}",
                "now": now,
            },
        )
        await session.exec(
            text(
                "INSERT INTO ordem_servico "
                "(id, cliente_id, veiculo_id, status, queixa_inicial, criado_em, "
                "atualizado_em, version) VALUES "
                "(:id, :customer_id, :vehicle_id, 'EM_DIAGNOSTICO', 'Ruído', "
                ":now, :now, 1)"
            ),
            params={
                "id": order_id,
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "now": now,
            },
        )
        await ReservaItemSagaRepo(session).salvar(saga)
    return saga


def _saga_result(saga: ReservaItemSaga, message_type: str) -> dict[str, object]:
    at = "2026-11-12T12:00:00Z"
    payload: dict[str, object]
    if message_type == "ResourceReserved":
        payload = {
            "reservation_id": str(uuid4()),
            "resource_id": str(saga.resource_id),
            "quantity": saga.quantity,
            "reserved_at": at,
            "resource_snapshot": {
                "name": "Filtro",
                "description": None,
                "unit_price": "20.00",
            },
        }
    else:
        payload = {
            "resource_id": str(saga.resource_id),
            "requested_quantity": saga.quantity,
            "available_quantity": 0,
            "decided_at": at,
            "resource_snapshot": {
                "name": "Filtro",
                "description": None,
                "unit_price": "20.00",
            },
        }
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": at,
        "correlation_id": str(saga.correlation_id),
        "causation_id": str(saga.ultimo_comando_message_id),
        "operation_id": str(saga.operation_id),
        "saga_id": str(saga.id),
        "payload": payload,
    }


async def _materialize(engine: AsyncEngine, saga: ReservaItemSaga, result: str) -> None:
    processor = PostgresInboxProcessor(
        _factory(engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=ReservationSagaResultHandler(),
    )
    assert (
        await processor.process(_saga_result(saga, result), receive_count=1)
        is ProcessingOutcome.PROCESSED
    )


async def _remove(engine: AsyncEngine, saga: ReservaItemSaga):
    return await RemoverItemDaOrdemServicoUseCase(
        OrdemServicoUnitOfWork(_factory(engine)())
    ).execute(
        str(saga.ordem_servico_id),
        str(saga.ordem_servico_item_id),
        correlation_id=uuid4(),
    )


async def test_item_reservado_so_e_cancelado_apos_resultado_autoritativo(
    db_engine: AsyncEngine,
) -> None:
    saga = await _seed_saga(db_engine)
    await _materialize(db_engine, saga, "ResourceReserved")

    accepted = await _remove(db_engine, saga)
    async with _factory(db_engine)() as session:
        operation = await session.get(
            DistributedOperationModel, UUID(accepted.operation_id)
        )
        item_before = await session.get(
            OrdemServicoItemModel, saga.ordem_servico_item_id
        )
        order_before = await session.get(OrdemServicoModel, saga.ordem_servico_id)
        outbox = (
            await session.exec(
                select(OutboxMessageModel).where(
                    OutboxMessageModel.aggregate_id == UUID(accepted.operation_id)
                )
            )
        ).scalar_one()

    assert accepted.state == "PENDING"
    assert operation is not None and operation.state == "PENDING"
    assert item_before is not None and item_before.status == "RESERVADO"
    assert order_before is not None and order_before.version == 2
    assert outbox.event_type == "ReleaseReservation.v1"
    assert outbox.saga_id is None

    result = {
        "message_id": str(uuid4()),
        "message_type": "ReservationReleased",
        "message_version": 1,
        "occurred_at": "2026-11-12T12:10:00Z",
        "correlation_id": str(operation.correlation_id),
        "causation_id": str(operation.command_message_id),
        "operation_id": str(operation.operation_id),
        "saga_id": None,
        "payload": {
            "reservation_id": str(operation.reservation_id),
            "resource_id": str(operation.resource_id),
            "quantity": operation.quantity,
            "released_at": "2026-11-12T12:10:00Z",
        },
    }
    processor = PostgresInboxProcessor(
        _factory(db_engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=DistributedStockOperationResultHandler(),
    )
    assert (
        await processor.process(result, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    duplicate = dict(result, message_id=str(uuid4()))
    assert (
        await processor.process(duplicate, receive_count=2)
        is ProcessingOutcome.DUPLICATE
    )

    async with _factory(db_engine)() as session:
        completed = await session.get(
            DistributedOperationModel, operation.operation_id
        )
        item_after = await session.get(
            OrdemServicoItemModel, saga.ordem_servico_item_id
        )
        order_after = await session.get(OrdemServicoModel, saga.ordem_servico_id)

    assert completed is not None
    assert (completed.state, completed.terminal_result) == (
        "COMPLETED",
        "RESERVATION_RELEASED",
    )
    assert item_after is not None and item_after.status == "CANCELADO"
    assert order_after is not None and order_after.version == 3


async def test_repeticao_da_remocao_retorna_mesma_operacao_sem_nova_outbox(
    db_engine: AsyncEngine,
) -> None:
    saga = await _seed_saga(db_engine)
    await _materialize(db_engine, saga, "ResourceReserved")

    first = await _remove(db_engine, saga)
    second = await _remove(db_engine, saga)

    assert second == first
    async with _factory(db_engine)() as session:
        operation_count = await session.scalar(
            select(func.count()).select_from(DistributedOperationModel)
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.aggregate_id == UUID(first.operation_id))
        )
    assert operation_count == 1
    assert outbox_count == 1


async def test_backorder_e_cancelado_localmente_sem_comando_de_liberacao(
    db_engine: AsyncEngine,
) -> None:
    saga = await _seed_saga(db_engine)
    await _materialize(db_engine, saga, "ItemBackordered")

    completed = await _remove(db_engine, saga)

    assert completed.state == "COMPLETED"
    assert completed.terminal_result == "NO_RESERVATION"
    assert completed.reservation_id is None
    async with _factory(db_engine)() as session:
        item = await session.get(OrdemServicoItemModel, saga.ordem_servico_item_id)
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.aggregate_id == UUID(completed.operation_id))
        )
    assert item is not None and item.status == "CANCELADO"
    assert outbox_count == 0


async def test_remocoes_concorrentes_convergem_para_uma_operacao(
    db_engine: AsyncEngine,
) -> None:
    saga = await _seed_saga(db_engine)
    await _materialize(db_engine, saga, "ResourceReserved")

    first, second = await asyncio.gather(
        _remove(db_engine, saga),
        _remove(db_engine, saga),
    )

    assert first == second
    async with _factory(db_engine)() as session:
        operation_count = await session.scalar(
            select(func.count()).select_from(DistributedOperationModel)
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.aggregate_id == UUID(first.operation_id))
        )
    assert operation_count == 1
    assert outbox_count == 1


async def test_falha_apos_resultado_reverte_inbox_item_operacao_e_versao(
    db_engine: AsyncEngine,
) -> None:
    saga = await _seed_saga(db_engine)
    await _materialize(db_engine, saga, "ResourceReserved")
    accepted = await _remove(db_engine, saga)
    async with _factory(db_engine)() as session:
        operation = await session.get(
            DistributedOperationModel, UUID(accepted.operation_id)
        )
    assert operation is not None
    result = {
        "message_id": str(uuid4()),
        "message_type": "ReservationReleased",
        "message_version": 1,
        "occurred_at": "2026-11-12T12:10:00Z",
        "correlation_id": str(operation.correlation_id),
        "causation_id": str(operation.command_message_id),
        "operation_id": str(operation.operation_id),
        "saga_id": None,
        "payload": {
            "reservation_id": str(operation.reservation_id),
            "resource_id": str(operation.resource_id),
            "quantity": operation.quantity,
            "released_at": "2026-11-12T12:10:00Z",
        },
    }

    class FailingHandler(DistributedStockOperationResultHandler):
        async def _after_effects(self, message_type, session) -> None:
            raise RuntimeError("falha injetada")

    failing = PostgresInboxProcessor(
        _factory(db_engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=FailingHandler(),
    )
    with pytest.raises(RuntimeError, match="falha injetada"):
        await failing.process(result, receive_count=1)

    async with _factory(db_engine)() as session:
        pending = await session.get(
            DistributedOperationModel, operation.operation_id
        )
        item = await session.get(OrdemServicoItemModel, saga.ordem_servico_item_id)
        order = await session.get(OrdemServicoModel, saga.ordem_servico_id)
        inbox_count = await session.scalar(
            text(
                "SELECT count(*) FROM inbox_message WHERE message_id = :message_id"
            ),
            params={"message_id": UUID(str(result["message_id"]))},
        )
    assert pending is not None and pending.state == "PENDING"
    assert item is not None and item.status == "RESERVADO"
    assert order is not None and order.version == 2
    assert inbox_count == 0

    retry = PostgresInboxProcessor(
        _factory(db_engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=DistributedStockOperationResultHandler(),
    )
    assert (
        await retry.process(result, receive_count=2)
        is ProcessingOutcome.PROCESSED
    )
