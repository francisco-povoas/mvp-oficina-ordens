"""Consumo distribuído confirma a entrega somente após todas as reservas."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
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
from app.modules.ordens_servico.application.use_cases.entregar_ordem_servico import (
    EntregarOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import ReservaItemSaga
from app.modules.ordens_servico.domain.exceptions import (
    DistributedOperationInvalidError,
)
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


async def _seed(engine: AsyncEngine, item_count: int) -> tuple[UUID, list[UUID]]:
    now = datetime.now(UTC)
    customer_id = uuid4()
    vehicle_id = uuid4()
    order_id = uuid4()
    item_ids: list[UUID] = []
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente "
                "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
                "criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Entrega', :cpf, '71999999999', true, :now, :now)"
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
                "plate": f"C{str(vehicle_id).replace('-', '')[:6]}",
                "now": now,
            },
        )
        await session.exec(
            text(
                "INSERT INTO ordem_servico "
                "(id, cliente_id, veiculo_id, status, queixa_inicial, diagnostico, "
                "criado_em, atualizado_em, iniciado_diagnostico_em, "
                "diagnostico_concluido_em, version, payment_id, payment_status, "
                "payment_amount, payment_confirmed_at, payment_quote_id, "
                "payment_quote_version) VALUES "
                "(:id, :customer_id, :vehicle_id, 'FINALIZADA', 'Ruído', 'Correia', "
                ":now, :now, :now, :now, 1, :payment_id, 'CONFIRMED', 200.00, "
                ":now, :quote_id, 1)"
            ),
            params={
                "id": order_id,
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "payment_id": uuid4(),
                "quote_id": uuid4(),
                "now": now,
            },
        )
        for index in range(item_count):
            item_id = uuid4()
            resource_id = uuid4()
            reservation_id = uuid4()
            item_ids.append(item_id)
            await session.exec(
                text(
                    "INSERT INTO ordem_servico_item "
                    "(id, ordem_servico_id, item_estoque_id, nome_item, tipo_item, "
                    "quantidade, valor_unitario, status) VALUES "
                    "(:id, :order_id, :resource_id, :name, 'PECA', 1, 50.00, 'EM_USO')"
                ),
                params={
                    "id": item_id,
                    "order_id": order_id,
                    "resource_id": resource_id,
                    "name": f"Peça {index}",
                },
            )
            saga = ReservaItemSaga.iniciar(
                saga_id=uuid4(),
                operation_id=uuid4(),
                ordem_servico_id=order_id,
                ordem_servico_item_id=item_id,
                resource_id=resource_id,
                quantity=1,
                correlation_id=uuid4(),
                idempotency_key=f"consume-{item_id}",
                request_hash="a" * 64,
                reserve_message_id=uuid4(),
                agora=now,
            ).registrar_reserva(
                reservation_id=reservation_id,
                message_id=uuid4(),
                resultado_em=now,
                resource_name=f"Peça {index}",
                resource_description=None,
                resource_unit_price=Decimal("50.00"),
            )
            await ReservaItemSagaRepo(session).salvar(saga)
    return order_id, item_ids


async def _start(engine: AsyncEngine, order_id: UUID):
    return await EntregarOrdemServicoUseCase(
        OrdemServicoUnitOfWork(_factory(engine)())
    ).execute(str(order_id), correlation_id=uuid4())


def _result(operation: DistributedOperationModel) -> dict[str, object]:
    return {
        "message_id": str(uuid4()),
        "message_type": "ReservationConsumed",
        "message_version": 1,
        "occurred_at": "2026-11-12T14:00:00Z",
        "correlation_id": str(operation.correlation_id),
        "causation_id": str(operation.command_message_id),
        "operation_id": str(operation.operation_id),
        "saga_id": None,
        "payload": {
            "reservation_id": str(operation.reservation_id),
            "resource_id": str(operation.resource_id),
            "quantity": operation.quantity,
            "consumed_at": "2026-11-12T14:00:00Z",
        },
    }


def _processor(engine: AsyncEngine, handler=None) -> PostgresInboxProcessor:
    return PostgresInboxProcessor(
        _factory(engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=handler or DistributedStockOperationResultHandler(),
    )


async def _operations(engine: AsyncEngine, ids: list[str]):
    async with _factory(engine)() as session:
        return [
            await session.get(DistributedOperationModel, UUID(operation_id))
            for operation_id in ids
        ]


async def test_uma_reserva_so_entrega_apos_confirmacao(db_engine: AsyncEngine) -> None:
    order_id, item_ids = await _seed(db_engine, 1)

    accepted = await _start(db_engine, order_id)
    repeated = await _start(db_engine, order_id)
    assert repeated == accepted
    assert accepted.state == "PENDING"
    operations = await _operations(db_engine, accepted.operation_ids)
    operation = operations[0]
    assert operation is not None
    async with _factory(db_engine)() as session:
        order_before = await session.get(OrdemServicoModel, order_id)
        item_before = await session.get(OrdemServicoItemModel, item_ids[0])
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(OutboxMessageModel)
            .where(OutboxMessageModel.aggregate_id == operation.operation_id)
        )
    assert order_before is not None and order_before.status == "FINALIZADA"
    assert item_before is not None and item_before.status == "EM_USO"
    assert outbox_count == 1

    result = _result(operation)
    assert (
        await _processor(db_engine).process(result, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    duplicate = dict(result, message_id=str(uuid4()))
    assert (
        await _processor(db_engine).process(duplicate, receive_count=2)
        is ProcessingOutcome.DUPLICATE
    )
    async with _factory(db_engine)() as session:
        order_after = await session.get(OrdemServicoModel, order_id)
        item_after = await session.get(OrdemServicoItemModel, item_ids[0])
    assert order_after is not None and order_after.status == "ENTREGUE"
    assert item_after is not None and item_after.status == "CONSUMIDO"


async def test_varias_reservas_nao_confirmam_entrega_prematuramente(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids = await _seed(db_engine, 2)
    accepted = await _start(db_engine, order_id)
    operations = await _operations(db_engine, accepted.operation_ids)
    first, second = operations
    assert first is not None and second is not None

    assert (
        await _processor(db_engine).process(_result(first), receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    async with _factory(db_engine)() as session:
        intermediate_order = await session.get(OrdemServicoModel, order_id)
        first_item = await session.get(OrdemServicoItemModel, item_ids[0])
        second_item = await session.get(OrdemServicoItemModel, item_ids[1])
    assert intermediate_order is not None and intermediate_order.status == "FINALIZADA"
    assert first_item is not None and first_item.status == "CONSUMIDO"
    assert second_item is not None and second_item.status == "EM_USO"

    assert (
        await _processor(db_engine).process(_result(second), receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    async with _factory(db_engine)() as session:
        delivered = await session.get(OrdemServicoModel, order_id)
    assert delivered is not None and delivered.status == "ENTREGUE"


async def test_resultado_de_outra_reserva_nao_altera_operacao_item_ou_entrega(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids = await _seed(db_engine, 1)
    accepted = await _start(db_engine, order_id)
    operation = (await _operations(db_engine, accepted.operation_ids))[0]
    assert operation is not None
    result = _result(operation)
    payload = dict(result["payload"])
    payload["reservation_id"] = str(uuid4())
    result["payload"] = payload

    with pytest.raises(DistributedOperationInvalidError, match="reservation_id"):
        await _processor(db_engine).process(result, receive_count=1)

    async with _factory(db_engine)() as session:
        pending = await session.get(
            DistributedOperationModel, operation.operation_id
        )
        item = await session.get(OrdemServicoItemModel, item_ids[0])
        order = await session.get(OrdemServicoModel, order_id)
    assert pending is not None and pending.state == "PENDING"
    assert item is not None and item.status == "EM_USO"
    assert order is not None and order.status == "FINALIZADA"


async def test_resultados_concorrentes_serializam_finalizacao(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids = await _seed(db_engine, 2)
    accepted = await _start(db_engine, order_id)
    operations = await _operations(db_engine, accepted.operation_ids)
    assert all(operation is not None for operation in operations)

    outcomes = await asyncio.gather(
        *(
            _processor(db_engine).process(_result(operation), receive_count=1)
            for operation in operations
            if operation is not None
        )
    )
    assert outcomes == [ProcessingOutcome.PROCESSED, ProcessingOutcome.PROCESSED]
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        items = [await session.get(OrdemServicoItemModel, item_id) for item_id in item_ids]
    assert order is not None and order.status == "ENTREGUE"
    assert all(item is not None and item.status == "CONSUMIDO" for item in items)


async def test_falha_de_commit_mantem_consumo_e_entrega_pendentes(
    db_engine: AsyncEngine,
) -> None:
    order_id, item_ids = await _seed(db_engine, 1)
    accepted = await _start(db_engine, order_id)
    operation = (await _operations(db_engine, accepted.operation_ids))[0]
    assert operation is not None
    result = _result(operation)

    class FailingHandler(DistributedStockOperationResultHandler):
        async def _after_effects(self, message_type, session) -> None:
            raise RuntimeError("falha injetada")

    with pytest.raises(RuntimeError, match="falha injetada"):
        await _processor(db_engine, FailingHandler()).process(result, receive_count=1)
    async with _factory(db_engine)() as session:
        order = await session.get(OrdemServicoModel, order_id)
        item = await session.get(OrdemServicoItemModel, item_ids[0])
        pending = await session.get(
            DistributedOperationModel, operation.operation_id
        )
    assert order is not None and order.status == "FINALIZADA"
    assert item is not None and item.status == "EM_USO"
    assert pending is not None and pending.state == "PENDING"

    assert (
        await _processor(db_engine).process(result, receive_count=2)
        is ProcessingOutcome.PROCESSED
    )
