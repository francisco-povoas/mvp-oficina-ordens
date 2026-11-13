"""Resultados de Recursos aplicados atomicamente à Saga e à OS."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.inbox_consumer import ProcessingOutcome
from app.consumer import RESOURCE_CONSUMER
from app.infrastructure.messaging.inbox import (
    InboxMessageModel,
    PostgresInboxProcessor,
)
from app.modules.ordens_servico.application.reservation_saga_result_handler import (
    ReservationSagaResultHandler,
)
from app.modules.ordens_servico.application.use_cases.cancelar_reserva_item_saga import (
    CancelarReservaItemSagaUseCase,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    ReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ReservaItemSagaTransicaoInvalidaError,
    ResultadoReservaItemSagaInvalidoError,
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
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.shared.infra.db.outbox_model import OutboxMessageModel


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _persist_context(engine: AsyncEngine) -> ReservaItemSaga:
    now = datetime.now(UTC)
    customer_id = uuid4()
    vehicle_id = uuid4()
    order_id = uuid4()
    command_id = uuid4()
    saga = ReservaItemSaga.iniciar(
        saga_id=uuid4(),
        operation_id=uuid4(),
        ordem_servico_id=order_id,
        ordem_servico_item_id=uuid4(),
        resource_id=uuid4(),
        quantity=2,
        correlation_id=uuid4(),
        idempotency_key="result-handler-test",
        request_hash="a" * 64,
        reserve_message_id=command_id,
        agora=now,
    )
    async with _factory(engine)() as session, session.begin():
        await session.exec(
            text(
                "INSERT INTO cliente "
                "(id, tipo_pessoa, nome_razao_social, cpf_cnpj, telefone, ativo, "
                "criado_em, atualizado_em) VALUES "
                "(:id, 'PF', 'Cliente Resultado Saga', :cpf, '71999999999', true, "
                ":now, :now)"
            ),
            params={
                "id": customer_id,
                "cpf": str(customer_id.int)[-11:],
                "now": now,
            },
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
                "(id, cliente_id, veiculo_id, status, queixa_inicial, criado_em, "
                "atualizado_em, version) VALUES "
                "(:id, :customer_id, :vehicle_id, 'EM_DIAGNOSTICO', 'Ruído', "
                ":now, :now, 2)"
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


def _envelope(saga: ReservaItemSaga, message_type: str) -> dict[str, object]:
    timestamp = "2026-11-11T15:00:00Z"
    snapshot = {
        "name": "Filtro autoritativo",
        "description": "Snapshot produzido por Recursos",
        "unit_price": "45.90",
    }
    payloads: dict[str, dict[str, object]] = {
        "ResourceReserved": {
            "reservation_id": str(saga.operation_id),
            "resource_id": str(saga.resource_id),
            "quantity": saga.quantity,
            "reserved_at": timestamp,
            "resource_snapshot": snapshot,
        },
        "ItemBackordered": {
            "resource_id": str(saga.resource_id),
            "requested_quantity": saga.quantity,
            "available_quantity": 1,
            "decided_at": timestamp,
            "resource_snapshot": snapshot,
        },
        "ReservationRejected": {
            "resource_id": str(saga.resource_id),
            "rejection_code": "RESOURCE_NOT_FOUND",
            "detail": "Recurso removido antes do processamento",
            "rejected_at": timestamp,
        },
        "ReservationReleased": {
            "reservation_id": str(saga.operation_id),
            "resource_id": str(saga.resource_id),
            "quantity": saga.quantity,
            "released_at": timestamp,
        },
    }
    return {
        "message_id": str(uuid4()),
        "message_type": message_type,
        "message_version": 1,
        "occurred_at": timestamp,
        "correlation_id": str(saga.correlation_id),
        "causation_id": str(saga.ultimo_comando_message_id),
        "operation_id": str(saga.operation_id),
        "saga_id": str(saga.id),
        "payload": payloads[message_type],
    }


async def _cancel(engine: AsyncEngine, saga: ReservaItemSaga):
    return await CancelarReservaItemSagaUseCase(
        OrdemServicoUnitOfWork(_factory(engine)())
    ).execute(saga.ordem_servico_id, saga.operation_id)


def _processor(
    engine: AsyncEngine,
    handler: ReservationSagaResultHandler | None = None,
) -> PostgresInboxProcessor:
    return PostgresInboxProcessor(
        _factory(engine),
        consumer_name=RESOURCE_CONSUMER,
        handler=handler or ReservationSagaResultHandler(),
    )


@pytest.mark.parametrize(
    ("message_type", "state", "result", "item_status"),
    [
        ("ResourceReserved", "COMPLETED", "RESERVED", "RESERVADO"),
        ("ItemBackordered", "COMPLETED", "BACKORDERED", "A_RECEBER"),
        (
            "ReservationRejected",
            "REJECTED",
            "RESERVATION_REJECTED",
            None,
        ),
    ],
)
async def test_aplica_resultado_terminal_autoritativo(
    db_engine: AsyncEngine,
    message_type: str,
    state: str,
    result: str,
    item_status: str | None,
) -> None:
    saga = await _persist_context(db_engine)
    envelope = _envelope(saga, message_type)

    outcome = await _processor(db_engine).process(envelope, receive_count=1)

    assert outcome is ProcessingOutcome.PROCESSED
    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        stored_item = await session.get(
            OrdemServicoItemModel, saga.ordem_servico_item_id
        )
        order = await session.get(OrdemServicoModel, saga.ordem_servico_id)
        inbox = (
            await session.exec(
                select(InboxMessageModel).where(
                    InboxMessageModel.message_id == UUID(str(envelope["message_id"]))
                )
            )
        ).scalar_one()

    assert stored_saga is not None
    assert (stored_saga.estado, stored_saga.terminal_result) == (state, result)
    assert stored_saga.ultimo_resultado_message_id == UUID(
        str(envelope["message_id"])
    )
    assert inbox.status == "PROCESSED"
    if item_status is None:
        assert stored_item is None
        assert order is not None and order.version == 2
        assert stored_saga.rejection_code == "RESOURCE_NOT_FOUND"
    else:
        assert stored_item is not None
        assert stored_item.status == item_status
        assert stored_item.nome_item == "Filtro autoritativo"
        assert stored_item.quantidade == saga.quantity
        assert order is not None and order.version == 3
    if message_type == "ResourceReserved":
        assert stored_saga.reservation_id == saga.operation_id


async def test_reentrega_e_nova_emissao_nao_materializam_item_duas_vezes(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    first = _envelope(saga, "ResourceReserved")
    second = deepcopy(first)
    second["message_id"] = str(uuid4())
    processor = _processor(db_engine)

    assert await processor.process(first, receive_count=1) is ProcessingOutcome.PROCESSED
    assert await processor.process(first, receive_count=2) is ProcessingOutcome.DUPLICATE
    assert await processor.process(second, receive_count=1) is ProcessingOutcome.DUPLICATE

    async with _factory(db_engine)() as session:
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
        order = await session.get(OrdemServicoModel, saga.ordem_servico_id)
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
    assert item_count == 1
    assert order is not None and order.version == 3
    assert stored_saga is not None and stored_saga.version == 2


async def test_resultado_alternativo_tardio_e_conflito_semantico(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    reserved = _envelope(saga, "ResourceReserved")
    backordered = _envelope(saga, "ItemBackordered")
    processor = _processor(db_engine)

    assert await processor.process(reserved, receive_count=1) is ProcessingOutcome.PROCESSED
    assert await processor.process(backordered, receive_count=1) is ProcessingOutcome.CONFLICT

    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
    assert stored_saga is not None
    assert stored_saga.terminal_result == "RESERVED"
    assert item_count == 1


@pytest.mark.parametrize("field", ["saga_id", "correlation_id", "causation_id"])
async def test_identidade_incorreta_reverte_inbox_e_estado(
    db_engine: AsyncEngine, field: str
) -> None:
    saga = await _persist_context(db_engine)
    envelope = _envelope(saga, "ResourceReserved")
    envelope[field] = str(uuid4())

    with pytest.raises(ResultadoReservaItemSagaInvalidoError):
        await _processor(db_engine).process(envelope, receive_count=1)

    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
    assert stored_saga is not None and stored_saga.estado == "PENDING"
    assert stored_saga.version == 1
    assert inbox_count == item_count == 0


class _FailAfterEffects(ReservationSagaResultHandler):
    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        raise RuntimeError(f"falha após efeitos de {message_type}")


async def test_falha_depois_dos_efeitos_reverte_e_reprocessa(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    envelope = _envelope(saga, "ResourceReserved")

    with pytest.raises(RuntimeError, match="falha após efeitos"):
        await _processor(db_engine, _FailAfterEffects()).process(
            envelope, receive_count=1
        )

    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
    assert stored_saga is not None and stored_saga.estado == "PENDING"
    assert item_count == inbox_count == 0

    assert (
        await _processor(db_engine).process(envelope, receive_count=2)
        is ProcessingOutcome.PROCESSED
    )


async def test_consumidores_concorrentes_aplicam_um_resultado(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    first = _envelope(saga, "ItemBackordered")
    second = deepcopy(first)
    second["message_id"] = str(uuid4())

    outcomes = await asyncio.gather(
        _processor(db_engine).process(first, receive_count=1),
        _processor(db_engine).process(second, receive_count=1),
    )

    assert set(outcomes) == {
        ProcessingOutcome.PROCESSED,
        ProcessingOutcome.DUPLICATE,
    }
    async with _factory(db_engine)() as session:
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
    assert item_count == 1
    assert stored_saga is not None and stored_saga.version == 2


async def test_cancelamento_pendente_e_repeticao_nao_presumem_reserva(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)

    first = await _cancel(db_engine, saga)
    repeated = await _cancel(db_engine, saga)

    assert first == repeated
    assert first.estado == "CANCEL_REQUESTED"
    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        outbox_count = await session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
    assert stored_saga is not None and stored_saga.version == 2
    assert stored_saga.cancellation_requested_at is not None
    assert outbox_count == item_count == 0


async def test_reserva_tardia_publica_release_e_resultado_conclui_compensacao(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    await _cancel(db_engine, saga)
    reserved = _envelope(saga, "ResourceReserved")

    assert (
        await _processor(db_engine).process(reserved, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )

    async with _factory(db_engine)() as session:
        releasing = await ReservaItemSagaRepo(session).obter_por_id(saga.id)
        outbox = (await session.exec(select(OutboxMessageModel))).scalar_one()
        item = await session.get(OrdemServicoItemModel, saga.ordem_servico_item_id)
    assert releasing is not None
    assert releasing.estado.value == "RELEASING"
    assert releasing.terminal_result is None
    assert outbox.event_type == "ReleaseReservation.v1"
    assert outbox.aggregate_id == saga.operation_id
    assert outbox.causation_id == UUID(str(reserved["message_id"]))
    assert outbox.published_at is None
    assert item is not None and item.status == "RESERVADO"

    async with _factory(db_engine)() as session, session.begin():
        published = await session.get(OutboxMessageModel, outbox.id)
        assert published is not None
        published.published_at = datetime.now(UTC)
        session.add(published)

    released = _envelope(releasing, "ReservationReleased")
    assert (
        await _processor(db_engine).process(released, receive_count=1)
        is ProcessingOutcome.PROCESSED
    )
    assert (
        await _processor(db_engine).process(released, receive_count=2)
        is ProcessingOutcome.DUPLICATE
    )

    async with _factory(db_engine)() as session:
        compensated = await session.get(ReservaItemSagaModel, saga.id)
        item = await session.get(OrdemServicoItemModel, saga.ordem_servico_item_id)
        order = await session.get(OrdemServicoModel, saga.ordem_servico_id)
        outbox_count = await session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
    assert compensated is not None
    assert (compensated.estado, compensated.terminal_result) == (
        "COMPENSATED",
        "RESERVATION_RELEASED",
    )
    assert item is not None and item.status == "CANCELADO"
    assert order is not None and order.version == 4
    assert outbox_count == 1


async def test_backorder_tardio_compensa_sem_release_fisico(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    await _cancel(db_engine, saga)

    assert (
        await _processor(db_engine).process(
            _envelope(saga, "ItemBackordered"), receive_count=1
        )
        is ProcessingOutcome.PROCESSED
    )

    async with _factory(db_engine)() as session:
        compensated = await session.get(ReservaItemSagaModel, saga.id)
        item = await session.get(OrdemServicoItemModel, saga.ordem_servico_item_id)
        outbox_count = await session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
    assert compensated is not None
    assert (compensated.estado, compensated.terminal_result) == (
        "COMPENSATED",
        "NO_RESERVATION",
    )
    assert item is not None and item.status == "CANCELADO"
    assert outbox_count == 0


async def test_rejeicao_tardia_prevalece_sem_release_fisico(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    await _cancel(db_engine, saga)

    assert (
        await _processor(db_engine).process(
            _envelope(saga, "ReservationRejected"), receive_count=1
        )
        is ProcessingOutcome.PROCESSED
    )

    async with _factory(db_engine)() as session:
        rejected = await session.get(ReservaItemSagaModel, saga.id)
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
        outbox_count = await session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
    assert rejected is not None and rejected.estado == "REJECTED"
    assert rejected.cancellation_requested_at is not None
    assert item_count == outbox_count == 0


async def test_falha_apos_criar_release_reverte_inbox_saga_item_e_outbox(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    await _cancel(db_engine, saga)
    reserved = _envelope(saga, "ResourceReserved")

    with pytest.raises(RuntimeError, match="falha após efeitos"):
        await _processor(db_engine, _FailAfterEffects()).process(
            reserved, receive_count=1
        )

    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        item_count = await session.scalar(
            select(func.count()).select_from(OrdemServicoItemModel)
        )
        outbox_count = await session.scalar(
            select(func.count()).select_from(OutboxMessageModel)
        )
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
    assert stored_saga is not None and stored_saga.estado == "CANCEL_REQUESTED"
    assert stored_saga.version == 2
    assert item_count == outbox_count == inbox_count == 0

    assert (
        await _processor(db_engine).process(reserved, receive_count=2)
        is ProcessingOutcome.PROCESSED
    )


async def test_resultado_release_antes_da_reserva_nao_reabre_saga(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)
    released = _envelope(saga, "ReservationReleased")

    with pytest.raises(ReservaItemSagaTransicaoInvalidaError):
        await _processor(db_engine).process(released, receive_count=1)

    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
        inbox_count = await session.scalar(
            select(func.count()).select_from(InboxMessageModel)
        )
    assert stored_saga is not None and stored_saga.estado == "PENDING"
    assert inbox_count == 0


async def test_cancelamentos_concorrentes_persistem_uma_transicao(
    db_engine: AsyncEngine,
) -> None:
    saga = await _persist_context(db_engine)

    first, second = await asyncio.gather(
        _cancel(db_engine, saga),
        _cancel(db_engine, saga),
    )

    assert first.estado == second.estado == "CANCEL_REQUESTED"
    async with _factory(db_engine)() as session:
        stored_saga = await session.get(ReservaItemSagaModel, saga.id)
    assert stored_saga is not None and stored_saga.version == 2
