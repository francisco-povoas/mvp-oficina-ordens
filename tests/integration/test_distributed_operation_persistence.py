"""Persistência e concorrência das coordenações em PostgreSQL real."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationResult,
    DistributedOperationState,
    DistributedOperationType,
)
from app.modules.ordens_servico.domain.exceptions import (
    DistributedOperationVersionConflictError,
)
from app.modules.ordens_servico.infrastructure.db.repositories.distributed_operation_repo import (
    PostgresDistributedOperationRepo,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.shared.outbox import OutboxMessage


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _persist_order_item(session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    now = datetime.now(UTC)
    cliente_id = uuid4()
    veiculo_id = uuid4()
    order_id = uuid4()
    item_id = uuid4()
    resource_id = uuid4()
    await session.exec(
        text(
            "INSERT INTO cliente "
            "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, criado_em, atualizado_em) "
            "VALUES (:id, 'PF', 'Cliente Operação', :cpf, '71999999999', true, :now, :now)"
        ),
        params={"id": cliente_id, "cpf": str(cliente_id.int)[-11:], "now": now},
    )
    await session.exec(
        text(
            "INSERT INTO veiculo "
            "(id, cliente_id, placa, marca, modelo, criado_em, atualizado_em) "
            "VALUES (:id, :cliente_id, :placa, 'Ford', 'Ka', :now, :now)"
        ),
        params={
            "id": veiculo_id,
            "cliente_id": cliente_id,
            "placa": f"D{str(veiculo_id).replace('-', '')[:6]}",
            "now": now,
        },
    )
    await session.exec(
        text(
            "INSERT INTO ordem_servico "
            "(id, cliente_id, veiculo_id, status, queixa_inicial, criado_em, atualizado_em, version) "
            "VALUES (:id, :cliente_id, :veiculo_id, 'EM_DIAGNOSTICO', 'Ruído', :now, :now, 1)"
        ),
        params={
            "id": order_id,
            "cliente_id": cliente_id,
            "veiculo_id": veiculo_id,
            "now": now,
        },
    )
    await session.exec(
        text(
            "INSERT INTO ordem_servico_item "
            "(id, ordem_servico_id, item_estoque_id, nome_item, tipo_item, quantidade, valor_unitario, status) "
            "VALUES (:id, :order_id, :resource_id, 'Filtro', 'PECA', 2, :price, 'RESERVADO')"
        ),
        params={
            "id": item_id,
            "order_id": order_id,
            "resource_id": resource_id,
            "price": Decimal("10.00"),
        },
    )
    await session.flush()
    return order_id, item_id, resource_id


def _operation(order_id: UUID, item_id: UUID, resource_id: UUID) -> DistributedOperation:
    return DistributedOperation.start(
        operation_id=uuid4(),
        operation_type=DistributedOperationType.RELEASE,
        ordem_servico_id=order_id,
        ordem_servico_item_id=item_id,
        resource_id=resource_id,
        quantity=2,
        business_key=f"RELEASE:{item_id}",
        request_hash="a" * 64,
        correlation_id=uuid4(),
        command_message_id=uuid4(),
        reservation_id=uuid4(),
    )


async def test_persiste_recupera_e_lista_operacao_pendente(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        order_id, item_id, resource_id = await _persist_order_item(session)
        operation = _operation(order_id, item_id, resource_id)
        await PostgresDistributedOperationRepo(session).save(operation)
        await session.commit()

    async with factory() as session:
        repo = PostgresDistributedOperationRepo(session)
        by_id = await repo.get_by_operation_id(operation.operation_id)
        by_key = await repo.get_by_business_key(operation.business_key)
        pending = await repo.list_pending_for_order(order_id)

    assert by_id == operation
    assert by_key == operation
    assert pending == [operation]


async def test_operacao_e_outbox_revertem_juntas(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        order_id, item_id, resource_id = await _persist_order_item(session)
        await session.commit()
    operation = _operation(order_id, item_id, resource_id)

    with pytest.raises(RuntimeError, match="rollback intencional"):
        async with OrdemServicoUnitOfWork(factory()) as uow:
            await uow.distributed_operation_repo.save(operation)
            await uow.outbox.add(
                OutboxMessage(
                    message_id=operation.command_message_id,  # type: ignore[arg-type]
                    aggregate_type="DistributedOperation",
                    aggregate_id=operation.operation_id,
                    event_type="ReleaseReservation.v1",
                    event_version=1,
                    payload={
                        "reservation_id": str(operation.reservation_id),
                        "resource_id": str(operation.resource_id),
                        "quantity": operation.quantity,
                        "reason": "ITEM_REMOVAL",
                    },
                    correlation_id=operation.correlation_id,
                    occurred_at=operation.command_created_at,  # type: ignore[arg-type]
                )
            )
            raise RuntimeError("rollback intencional")

    async with factory() as session:
        assert (
            await PostgresDistributedOperationRepo(session).get_by_operation_id(
                operation.operation_id
            )
            is None
        )
        count = await session.scalar(text("SELECT count(*) FROM outbox_message"))
        assert count == 0


async def test_controle_otimista_rejeita_lost_update(db_engine: AsyncEngine) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        order_id, item_id, resource_id = await _persist_order_item(session)
        operation = _operation(order_id, item_id, resource_id)
        await PostgresDistributedOperationRepo(session).save(operation)
        await session.commit()

    async with factory() as session_a, factory() as session_b:
        repo_a = PostgresDistributedOperationRepo(session_a)
        repo_b = PostgresDistributedOperationRepo(session_b)
        op_a = await repo_a.get_by_operation_id(operation.operation_id)
        op_b = await repo_b.get_by_operation_id(operation.operation_id)
        assert op_a is not None and op_b is not None
        reservation_id = operation.reservation_id
        assert reservation_id is not None

        completed_a = op_a.complete(
            result=DistributedOperationResult.RESERVATION_RELEASED,
            result_message_id=uuid4(),
            reservation_id=reservation_id,
            received_at=datetime.now(UTC),
        )
        completed_b = op_b.complete(
            result=DistributedOperationResult.RESERVATION_RELEASED,
            result_message_id=uuid4(),
            reservation_id=reservation_id,
            received_at=datetime.now(UTC),
        )
        await repo_a.update(completed_a)
        await session_a.commit()
        with pytest.raises(DistributedOperationVersionConflictError):
            await repo_b.update(completed_b)
        await session_b.rollback()

    async with factory() as session:
        persisted = await PostgresDistributedOperationRepo(session).get_by_operation_id(
            operation.operation_id
        )
    assert persisted is not None
    assert persisted.state is DistributedOperationState.COMPLETED
    assert persisted.version == 2


async def test_chave_semantica_serializa_criacoes_concorrentes(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        order_id, item_id, resource_id = await _persist_order_item(session)
        await session.commit()
    first = _operation(order_id, item_id, resource_id)
    second = _operation(order_id, item_id, resource_id)

    async with factory() as session_a, factory() as session_b:
        await PostgresDistributedOperationRepo(session_a).save(first)
        await session_a.commit()
        await PostgresDistributedOperationRepo(session_b).save(second)
        with pytest.raises(IntegrityError):
            await session_b.commit()
        await session_b.rollback()

    async with factory() as session:
        persisted = await PostgresDistributedOperationRepo(session).get_by_business_key(
            first.business_key
        )
    assert persisted == first
