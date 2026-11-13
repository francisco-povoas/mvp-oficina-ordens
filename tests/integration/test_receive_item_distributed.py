"""Recebimento de backorder coordenado por Outbox/Inbox."""

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
from app.modules.ordens_servico.application.use_cases.confirmar_recebimento_item import (
    ConfirmarRecebimentoItemDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import ReservaItemSaga
from app.modules.ordens_servico.domain.exceptions import DistributedOperationInvalidError
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
        idempotency_key=f"receive-{uuid4()}",
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
                "(:id, 'PF', 'Cliente Recebimento', :cpf, '71999999999', true, "
                ":now, :now)"
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
                "plate": f"B{str(vehicle_id).replace('-', '')[:6]}",
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


async def _materialize(engine: AsyncEngine, saga: ReservaItemSaga) -> None:
    at = "2026-11-12T12:00:00Z"
    envelope = {
        "message_id": str(uuid4()),
        "message_type": "ItemBackordered",
        "message_version": 1,
        "occurred_at": at,
        "correlation_id": str(saga.correlation_id),
        "causation_id": str(saga.ultimo_comando_message_id),
        "operation_id": str(saga.operation_id),
        "saga_id": str(saga.id),
        "payload": {
            "resource_id": str(saga.resource_id),
            "requested_quantity": saga.quantity,
            "available_quantity": 0,
            "decided_at": at,
            "resource_snapshot": {
                "name": "Filtro",
                "description": None,
                "unit_price": "20.00",
            },
        },
    }
    processor = PostgresInboxProcessor(
        _factory(engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=ReservationSagaResultHandler(),
    )
    assert (
        await processor.process(envelope, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )


async def _backorder_context(db_engine):
    saga = await _seed_saga(db_engine)
    await _materialize(db_engine, saga)
    async with _factory(db_engine)() as session, session.begin():
        order = await session.get(OrdemServicoModel, saga.ordem_servico_id)
        assert order is not None
        order.status = "AGUARDANDO_ITENS"
        order.version += 1
        session.add(order)
    return saga


async def _receive(db_engine, saga):
    return await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
        OrdemServicoUnitOfWork(_factory(db_engine)())
    ).execute(
        str(saga.ordem_servico_id),
        str(saga.ordem_servico_item_id),
        correlation_id=uuid4(),
    )


def _result(operation: DistributedOperationModel) -> dict[str, object]:
    return {
        "message_id": str(uuid4()),
        "message_type": "BackorderedItemReceived",
        "message_version": 1,
        "occurred_at": "2026-11-12T13:00:00Z",
        "correlation_id": str(operation.correlation_id),
        "causation_id": str(operation.command_message_id),
        "operation_id": str(operation.operation_id),
        "saga_id": None,
        "payload": {
            "reservation_id": str(uuid4()),
            "resource_id": str(operation.resource_id),
            "quantity": operation.quantity,
            "received_at": "2026-11-12T13:00:00Z",
        },
    }


async def test_item_so_fica_reservado_apos_resultado_autoritativo(db_engine) -> None:
    saga = await _backorder_context(db_engine)

    accepted = await _receive(db_engine, saga)
    repeated = await _receive(db_engine, saga)
    assert repeated == accepted
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

    assert operation is not None and operation.state == "PENDING"
    assert item_before is not None and item_before.status == "A_RECEBER"
    assert order_before is not None and order_before.status == "AGUARDANDO_ITENS"
    assert outbox.event_type == "ReceiveBackorderedItem.v1"
    async with _factory(db_engine)() as session:
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.aggregate_id == UUID(accepted.operation_id))
        )
    assert outbox_count == 1

    result = _result(operation)
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
    assert completed.terminal_result == "BACKORDERED_ITEM_RECEIVED"
    assert completed.reservation_id == UUID(str(result["payload"]["reservation_id"]))  # type: ignore[index]
    assert item_after is not None and item_after.status == "RESERVADO"
    assert order_after is not None and order_after.status == "APROVADA"


async def test_outro_backorder_mantem_os_aguardando_itens(db_engine) -> None:
    saga = await _backorder_context(db_engine)
    other_item_id = uuid4()
    async with _factory(db_engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO ordem_servico_item "
                "(id, ordem_servico_id, item_estoque_id, nome_item, tipo_item, "
                "quantidade, valor_unitario, status) VALUES "
                "(:id, :order_id, :resource_id, 'Outro', 'PECA', 1, 10.00, 'A_RECEBER')"
            ),
            params={
                "id": other_item_id,
                "order_id": saga.ordem_servico_id,
                "resource_id": uuid4(),
            },
        )
    accepted = await _receive(db_engine, saga)
    async with _factory(db_engine)() as session:
        operation = await session.get(
            DistributedOperationModel, UUID(accepted.operation_id)
        )
    assert operation is not None
    processor = PostgresInboxProcessor(
        _factory(db_engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=DistributedStockOperationResultHandler(),
    )
    assert (
        await processor.process(_result(operation), receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, saga.ordem_servico_id)
    assert order is not None and order.status == "AGUARDANDO_ITENS"


async def test_resultado_de_outra_operacao_nao_altera_item(db_engine) -> None:
    saga = await _backorder_context(db_engine)
    accepted = await _receive(db_engine, saga)
    async with _factory(db_engine)() as session:
        operation = await session.get(
            DistributedOperationModel, UUID(accepted.operation_id)
        )
    assert operation is not None
    result = _result(operation)
    result["operation_id"] = str(uuid4())
    processor = PostgresInboxProcessor(
        _factory(db_engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=DistributedStockOperationResultHandler(),
    )
    with pytest.raises(DistributedOperationInvalidError, match="não encontrada"):
        await processor.process(result, receive_count=1)
    async with _factory(db_engine)() as session:
        item = await session.get(OrdemServicoItemModel, saga.ordem_servico_item_id)
        inbox_count = await session.scalar(
            select(func.count()).select_from(DistributedOperationModel)
        )
    assert item is not None and item.status == "A_RECEBER"
    assert inbox_count == 1


async def test_falha_de_persistencia_preserva_recebimento_pendente(db_engine) -> None:
    saga = await _backorder_context(db_engine)
    accepted = await _receive(db_engine, saga)
    async with _factory(db_engine)() as session:
        operation = await session.get(
            DistributedOperationModel, UUID(accepted.operation_id)
        )
    assert operation is not None
    result = _result(operation)

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
    assert pending is not None and pending.state == "PENDING"
    assert item is not None and item.status == "A_RECEBER"
    assert order is not None and order.status == "AGUARDANDO_ITENS"

    retry = PostgresInboxProcessor(
        _factory(db_engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=DistributedStockOperationResultHandler(),
    )
    assert (
        await retry.process(result, receive_count=2)
        is ProcessingOutcome.PROCESSED
    )
