"""Fluxo real da Saga entre duas bases PostgreSQL e ElasticMQ."""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import boto3
import pytest
from botocore.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.integrations.resources import ItemType, RecursoSnapshot
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    AdicionarItemNaOSRequest,
)
from app.modules.ordens_servico.application.use_cases.adicionar_item_na_ordem_servico import (
    AdicionarItemNaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.cancelar_reserva_item_saga import (
    CancelarReservaItemSagaUseCase,
)
from app.modules.ordens_servico.application.use_cases.confirmar_recebimento_item import (
    ConfirmarRecebimentoItemDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.entregar_ordem_servico import (
    EntregarOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.remover_item_da_ordem_servico import (
    RemoverItemDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.shared.value_objects.id import ID

if "RECURSOS_DATABASE_URL" not in os.environ:
    pytest.skip(
        "requer compose.saga.test.yaml com duas bases e ElasticMQ",
        allow_module_level=True,
    )

ORDENS_DATABASE_URL = os.environ["DATABASE_URL"]
RECURSOS_DATABASE_URL = os.environ["RECURSOS_DATABASE_URL"]
SQS_ENDPOINT = os.environ["SQS_ENDPOINT_URL"]
COMMAND_QUEUE = os.environ["SQS_ORDENS_TO_RECURSOS_URL"]
RESULT_QUEUE = os.environ["SQS_RECURSOS_TO_ORDENS_URL"]
READY_FILE = Path(os.environ["SAGA_READY_FILE"])

RESOURCE_IDS = {
    "reserved": UUID("10000000-0000-0000-0000-000000000001"),
    "backordered": UUID("10000000-0000-0000-0000-000000000002"),
    "rejected": UUID("10000000-0000-0000-0000-000000000003"),
    "compensated": UUID("10000000-0000-0000-0000-000000000004"),
}


@dataclass(frozen=True)
class _Catalog:
    resource_id: UUID

    async def obter_recurso_por_id(self, recurso_id: ID) -> RecursoSnapshot:
        assert recurso_id.value == self.resource_id
        return RecursoSnapshot(
            id=recurso_id,
            tipo=ItemType.PECA,
            nome=f"Peça {str(recurso_id.value)[-4:]}",
            descricao="Snapshot da integração distribuída",
            valor_unitario=Decimal("25.00"),
        )


def _factory(url: str) -> tuple[object, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url)
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def _seed_order(session: AsyncSession, label: str) -> UUID:
    now = datetime.now(UTC)
    customer_id = uuid4()
    vehicle_id = uuid4()
    order_id = uuid4()
    await session.exec(
        text(
            "INSERT INTO cliente "
            "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
            "criado_em, atualizado_em) VALUES "
            "(:id, 'PF', :name, :cpf, '71999999999', true, :now, :now)"
        ),
        params={
            "id": customer_id,
            "name": f"Cliente {label}",
            "cpf": str(customer_id.int)[-11:],
            "now": now,
        },
    )
    await session.exec(
        text(
            "INSERT INTO veiculo "
            "(id, cliente_id, placa, marca, modelo, criado_em, atualizado_em) "
            "VALUES (:id, :customer, :plate, 'Ford', 'Ka', :now, :now)"
        ),
        params={
            "id": vehicle_id,
            "customer": customer_id,
            "plate": f"S{str(vehicle_id).replace('-', '')[:6]}",
            "now": now,
        },
    )
    await session.exec(
        text(
            "INSERT INTO ordem_servico "
            "(id, cliente_id, veiculo_id, status, queixa_inicial, diagnostico, "
            "criado_em, atualizado_em, version) VALUES "
            "(:id, :customer, :vehicle, 'EM_DIAGNOSTICO', 'Ruído', 'Diagnóstico', "
            ":now, :now, 1)"
        ),
        params={
            "id": order_id,
            "customer": customer_id,
            "vehicle": vehicle_id,
            "now": now,
        },
    )
    await session.commit()
    return order_id


async def _seed_resources(factory: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(UTC)
    balances = {
        "reserved": 5,
        "backordered": 1,
        "compensated": 5,
    }
    async with factory() as session, session.begin():
        for label, available in balances.items():
            resource_id = RESOURCE_IDS[label]
            await session.exec(
                text(
                    "INSERT INTO item_estoque "
                    "(id, tipo, nome, descricao, codigo, quantidade_disponivel, "
                    "quantidade_reservada, quantidade_minima, valor_unitario, ativo, "
                    "criado_em, atualizado_em) VALUES "
                    "(:id, 'PECA', :name, :description, :code, :available, 0, 0, "
                    "25.00, true, :now, :now)"
                ),
                params={
                    "id": resource_id,
                    "name": f"Peça {str(resource_id)[-4:]}",
                    "description": "Snapshot da integração distribuída",
                    "code": f"SAGA-{label}",
                    "available": available,
                    "now": now,
                },
            )


async def _seed_sagas(
    factory: async_sessionmaker[AsyncSession],
) -> dict[str, UUID]:
    operations: dict[str, UUID] = {}
    for label, resource_id in RESOURCE_IDS.items():
        async with factory() as session:
            order_id = await _seed_order(session, label)
        response = await AdicionarItemNaOrdemServicoUseCase(
            uow=OrdemServicoUnitOfWork(factory()),
            recursos=_Catalog(resource_id),
        ).execute(
            str(order_id),
            AdicionarItemNaOSRequest(
                item_estoque_id=str(resource_id),
                quantidade=2,
            ),
            idempotency_key=f"distributed-{label}",
            correlation_id=uuid4(),
        )
        operation_id = UUID(response.operation_id)
        operations[label] = operation_id
        if label == "compensated":
            await CancelarReservaItemSagaUseCase(
                OrdemServicoUnitOfWork(factory())
            ).execute(order_id, operation_id)
    return operations


async def _query_one(factory, statement: str, **params):
    async with factory() as session:
        return (await session.exec(text(statement), params=params)).mappings().one()


async def _query_all(factory, statement: str, **params):
    async with factory() as session:
        return list((await session.exec(text(statement), params=params)).mappings())


async def _wait_for_terminal(factory, operations: dict[str, UUID]) -> None:
    deadline = asyncio.get_running_loop().time() + 40
    while asyncio.get_running_loop().time() < deadline:
        rows = await _query_all(
            factory,
            "SELECT operation_id, estado, terminal_result FROM reserva_item_saga",
        )
        by_operation = {row["operation_id"]: row for row in rows}
        if len(by_operation) == 4 and all(
            by_operation[operation_id]["estado"]
            in {"COMPLETED", "COMPENSATED", "REJECTED"}
            for operation_id in operations.values()
        ):
            return
        await asyncio.sleep(0.2)
    raise AssertionError("Sagas não atingiram estado terminal no prazo")


def _send_semantic_duplicate(outbox: dict[str, object]) -> None:
    event_type = str(outbox["event_type"])
    envelope = {
        "message_id": str(uuid4()),
        "message_type": event_type.removesuffix(".v1"),
        "message_version": 1,
        "occurred_at": outbox["occurred_at"].isoformat().replace("+00:00", "Z"),
        "correlation_id": str(outbox["correlation_id"]),
        "causation_id": None,
        "operation_id": str(outbox["aggregate_id"]),
        "saga_id": (
            str(outbox["saga_id"])
            if outbox["saga_id"] is not None
            else None
        ),
        "payload": json.loads(str(outbox["payload"])),
    }
    client = boto3.client(
        "sqs",
        endpoint_url=SQS_ENDPOINT,
        region_name="us-east-1",
        config=Config(retries={"max_attempts": 1}),
    )
    try:
        client.send_message(
            QueueUrl=COMMAND_QUEUE,
            MessageBody=json.dumps(envelope),
            MessageGroupId=str(outbox["aggregate_id"]),
            MessageDeduplicationId=envelope["message_id"],
        )
    finally:
        client.close()


async def _wait_for_duplicate(factory, message_count: int) -> None:
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        row = await _query_one(
            factory,
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE status = 'DUPLICATE') AS duplicates "
            "FROM inbox_message",
        )
        if row["total"] == message_count and row["duplicates"] >= 1:
            return
        await asyncio.sleep(0.2)
    raise AssertionError("Duplicidade semântica não foi consolidada no prazo")


async def _wait_for_distributed_operations(
    factory: async_sessionmaker[AsyncSession],
    operation_ids: list[UUID],
) -> None:
    deadline = asyncio.get_running_loop().time() + 40
    while asyncio.get_running_loop().time() < deadline:
        rows = await _query_all(
            factory,
            "SELECT operation_id, state FROM distributed_operation "
            "WHERE operation_id = ANY(:operation_ids)",
            operation_ids=operation_ids,
        )
        if len(rows) == len(operation_ids) and all(
            row["state"] == "COMPLETED" for row in rows
        ):
            return
        await asyncio.sleep(0.2)
    raise AssertionError("Operações distribuídas não concluíram no prazo")


async def test_fluxos_distribuidos_persistem_efeitos_e_idempotencia() -> None:
    ordens_engine, ordens_factory = _factory(ORDENS_DATABASE_URL)
    recursos_engine, recursos_factory = _factory(RECURSOS_DATABASE_URL)
    try:
        await _seed_resources(recursos_factory)
        operations = await _seed_sagas(ordens_factory)

        pending = await _query_one(
            ordens_factory,
            "SELECT count(*) AS total FROM outbox_message WHERE published_at IS NULL",
        )
        assert pending["total"] == 4
        READY_FILE.write_text("ready", encoding="utf-8")

        await _wait_for_terminal(ordens_factory, operations)

        expected = {
            "reserved": ("COMPLETED", "RESERVED", "RESERVADO"),
            "backordered": ("COMPLETED", "BACKORDERED", "A_RECEBER"),
            "rejected": ("REJECTED", "RESERVATION_REJECTED", None),
            "compensated": (
                "COMPENSATED",
                "RESERVATION_RELEASED",
                "CANCELADO",
            ),
        }
        for label, operation_id in operations.items():
            row = await _query_one(
                ordens_factory,
                "SELECT s.estado, s.terminal_result, i.status AS item_status "
                "FROM reserva_item_saga s LEFT JOIN ordem_servico_item i "
                "ON i.id = s.ordem_servico_item_id WHERE s.operation_id = :operation",
                operation=operation_id,
            )
            assert (row["estado"], row["terminal_result"], row["item_status"]) == expected[
                label
            ]

        resource_expectations = {
            "reserved": (3, 2, "RESERVADA"),
            "backordered": (1, 0, "SEM_ESTOQUE"),
            "compensated": (5, 0, "LIBERADA"),
        }
        for label, expectation in resource_expectations.items():
            row = await _query_one(
                recursos_factory,
                "SELECT i.quantidade_disponivel, i.quantidade_reservada, r.status "
                "FROM item_estoque i JOIN reserva_estoque r "
                "ON r.item_estoque_id = i.id WHERE r.operation_id = :operation",
                operation=operations[label],
            )
            assert (
                row["quantidade_disponivel"],
                row["quantidade_reservada"],
                row["status"],
            ) == expectation

        ordens_counts = await _query_one(
            ordens_factory,
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE published_at IS NOT NULL) AS published "
            "FROM outbox_message",
        )
        recursos_counts = await _query_one(
            recursos_factory,
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE published_at IS NOT NULL) AS published "
            "FROM outbox_message",
        )
        assert tuple(ordens_counts.values()) == (5, 5)
        assert tuple(recursos_counts.values()) == (5, 5)

        original = await _query_one(
            ordens_factory,
            "SELECT * FROM outbox_message WHERE aggregate_id = :operation "
            "AND event_type = 'ReserveItem.v1'",
            operation=operations["reserved"],
        )
        _send_semantic_duplicate(dict(original))
        await _wait_for_duplicate(recursos_factory, 6)

        outbox_after_duplicate = await _query_one(
            recursos_factory,
            "SELECT count(*) AS total FROM outbox_message",
        )
        balance_after_duplicate = await _query_one(
            recursos_factory,
            "SELECT quantidade_disponivel, quantidade_reservada FROM item_estoque "
            "WHERE id = :resource_id",
            resource_id=RESOURCE_IDS["reserved"],
        )
        assert outbox_after_duplicate["total"] == 5
        assert tuple(balance_after_duplicate.values()) == (3, 2)

        reserved_context = await _query_one(
            ordens_factory,
            "SELECT ordem_servico_id, ordem_servico_item_id FROM reserva_item_saga "
            "WHERE operation_id = :operation",
            operation=operations["reserved"],
        )
        backordered_context = await _query_one(
            ordens_factory,
            "SELECT ordem_servico_id, ordem_servico_item_id FROM reserva_item_saga "
            "WHERE operation_id = :operation",
            operation=operations["backordered"],
        )
        async with ordens_factory() as session, session.begin():
            await session.exec(
                text(
                    "UPDATE ordem_servico SET status = 'AGUARDANDO_ITENS', "
                    "version = version + 1, atualizado_em = :now WHERE id = :order_id"
                ),
                params={
                    "now": datetime.now(UTC),
                    "order_id": backordered_context["ordem_servico_id"],
                },
            )
        release, receive = await asyncio.gather(
            RemoverItemDaOrdemServicoUseCase(
                OrdemServicoUnitOfWork(ordens_factory())
            ).execute(
                str(reserved_context["ordem_servico_id"]),
                str(reserved_context["ordem_servico_item_id"]),
                correlation_id=uuid4(),
            ),
            ConfirmarRecebimentoItemDaOrdemServicoUseCase(
                OrdemServicoUnitOfWork(ordens_factory())
            ).execute(
                str(backordered_context["ordem_servico_id"]),
                str(backordered_context["ordem_servico_item_id"]),
                correlation_id=uuid4(),
            ),
        )
        assert release.state == "PENDING"
        assert receive.state == "PENDING"
        await _wait_for_distributed_operations(
            ordens_factory,
            [UUID(release.operation_id), UUID(receive.operation_id)],
        )

        released = await _query_one(
            ordens_factory,
            "SELECT d.terminal_result, i.status AS item_status "
            "FROM distributed_operation d JOIN ordem_servico_item i "
            "ON i.id = d.ordem_servico_item_id "
            "WHERE d.operation_id = :operation",
            operation=UUID(release.operation_id),
        )
        received = await _query_one(
            ordens_factory,
            "SELECT d.terminal_result, d.reservation_id, i.status AS item_status "
            "FROM distributed_operation d JOIN ordem_servico_item i "
            "ON i.id = d.ordem_servico_item_id "
            "WHERE d.operation_id = :operation",
            operation=UUID(receive.operation_id),
        )
        assert tuple(released.values()) == ("RESERVATION_RELEASED", "CANCELADO")
        assert received["terminal_result"] == "BACKORDERED_ITEM_RECEIVED"
        assert received["reservation_id"] == operations["backordered"]
        assert received["item_status"] == "RESERVADO"

        released_stock = await _query_one(
            recursos_factory,
            "SELECT i.quantidade_disponivel, i.quantidade_reservada, r.status "
            "FROM item_estoque i JOIN reserva_estoque r "
            "ON r.item_estoque_id = i.id WHERE r.operation_id = :reservation",
            reservation=operations["reserved"],
        )
        received_stock = await _query_one(
            recursos_factory,
            "SELECT i.quantidade_disponivel, i.quantidade_reservada, r.status "
            "FROM item_estoque i JOIN reserva_estoque r "
            "ON r.item_estoque_id = i.id WHERE r.operation_id = :reservation",
            reservation=operations["backordered"],
        )
        assert tuple(released_stock.values()) == (5, 0, "LIBERADA")
        assert tuple(received_stock.values()) == (1, 2, "RESERVADA")

        now = datetime.now(UTC)
        async with ordens_factory() as session, session.begin():
            await session.exec(
                text(
                    "UPDATE ordem_servico SET status = 'FINALIZADA', "
                    "payment_id = :payment_id, payment_status = 'CONFIRMED', "
                    "payment_amount = 50.00, payment_confirmed_at = :now, "
                    "payment_quote_id = :quote_id, payment_quote_version = 1, "
                    "atualizado_em = :now, "
                    "version = version + 1 WHERE id = :order_id"
                ),
                params={
                    "payment_id": uuid4(),
                    "quote_id": uuid4(),
                    "now": now,
                    "order_id": backordered_context["ordem_servico_id"],
                },
            )
            await session.exec(
                text(
                    "UPDATE ordem_servico_item SET status = 'EM_USO' "
                    "WHERE id = :item_id"
                ),
                params={"item_id": backordered_context["ordem_servico_item_id"]},
            )

        delivery = await EntregarOrdemServicoUseCase(
            OrdemServicoUnitOfWork(ordens_factory())
        ).execute(
            str(backordered_context["ordem_servico_id"]),
            correlation_id=uuid4(),
        )
        assert delivery.state == "PENDING"
        assert delivery.order_status == "FINALIZADA"
        assert len(delivery.operation_ids) == 1
        consume_operation_id = UUID(delivery.operation_ids[0])
        await _wait_for_distributed_operations(
            ordens_factory,
            [consume_operation_id],
        )

        delivered = await _query_one(
            ordens_factory,
            "SELECT o.status AS order_status, i.status AS item_status, "
            "d.terminal_result FROM ordem_servico o "
            "JOIN ordem_servico_item i ON i.ordem_servico_id = o.id "
            "JOIN distributed_operation d ON d.ordem_servico_item_id = i.id "
            "WHERE d.operation_id = :operation",
            operation=consume_operation_id,
        )
        consumed_stock = await _query_one(
            recursos_factory,
            "SELECT i.quantidade_disponivel, i.quantidade_reservada, r.status "
            "FROM item_estoque i JOIN reserva_estoque r "
            "ON r.item_estoque_id = i.id WHERE r.operation_id = :reservation",
            reservation=operations["backordered"],
        )
        assert tuple(delivered.values()) == (
            "ENTREGUE",
            "CONSUMIDO",
            "RESERVATION_CONSUMED",
        )
        assert tuple(consumed_stock.values()) == (1, 0, "CONSUMIDA")

        consume_command = await _query_one(
            ordens_factory,
            "SELECT * FROM outbox_message WHERE aggregate_id = :operation "
            "AND event_type = 'ConsumeReservation.v1'",
            operation=consume_operation_id,
        )
        _send_semantic_duplicate(dict(consume_command))
        await _wait_for_duplicate(recursos_factory, 10)
        consume_outbox_count = await _query_one(
            recursos_factory,
            "SELECT count(*) AS total FROM outbox_message",
        )
        stock_after_consume_duplicate = await _query_one(
            recursos_factory,
            "SELECT quantidade_disponivel, quantidade_reservada FROM item_estoque "
            "WHERE id = :resource_id",
            resource_id=RESOURCE_IDS["backordered"],
        )
        assert consume_outbox_count["total"] == 8
        assert tuple(stock_after_consume_duplicate.values()) == (1, 0)

        ordens_inbox = await _query_one(
            ordens_factory,
            "SELECT count(*) AS total, "
            "count(*) FILTER (WHERE status = 'PROCESSED') AS processed "
            "FROM inbox_message",
        )
        assert tuple(ordens_inbox.values()) == (8, 8)
    finally:
        await ordens_engine.dispose()
        await recursos_engine.dispose()
