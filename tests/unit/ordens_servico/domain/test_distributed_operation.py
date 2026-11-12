"""Invariantes das coordenações distribuídas leves."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationResult,
    DistributedOperationState,
    DistributedOperationType,
)
from app.modules.ordens_servico.domain.exceptions import (
    DistributedOperationIdempotencyConflictError,
    DistributedOperationTransitionError,
)


def _pending(
    operation_type: DistributedOperationType = DistributedOperationType.RELEASE,
) -> DistributedOperation:
    item_id = uuid4()
    reservation_id = uuid4() if operation_type is not DistributedOperationType.RECEIVE else None
    return DistributedOperation.start(
        operation_id=uuid4(),
        operation_type=operation_type,
        ordem_servico_id=uuid4(),
        ordem_servico_item_id=item_id,
        resource_id=uuid4(),
        quantity=2,
        business_key=f"{operation_type.value}:{item_id}",
        request_hash="a" * 64,
        correlation_id=uuid4(),
        command_message_id=uuid4(),
        reservation_id=reservation_id,
    )


def test_inicia_pendente_com_comando_versionado() -> None:
    operation = _pending(DistributedOperationType.RECEIVE)

    assert operation.state is DistributedOperationState.PENDING
    assert operation.command_type == "ReceiveBackorderedItem"
    assert operation.command_message_id is not None
    assert operation.terminal_result is None
    assert operation.version == 1


def test_repeticao_semantica_aceita_mesmo_hash_e_rejeita_payload_divergente() -> None:
    operation = _pending()

    operation.validate_repetition("a" * 64)
    with pytest.raises(DistributedOperationIdempotencyConflictError):
        operation.validate_repetition("b" * 64)


def test_resultado_autoritativo_conclui_sem_reabrir_operacao() -> None:
    operation = _pending(DistributedOperationType.CONSUME)
    result_message_id = uuid4()
    reservation_id = operation.reservation_id
    assert reservation_id is not None
    received_at = datetime.now(UTC)

    completed = operation.complete(
        result=DistributedOperationResult.RESERVATION_CONSUMED,
        result_message_id=result_message_id,
        reservation_id=reservation_id,
        received_at=received_at,
    )

    assert completed.state is DistributedOperationState.COMPLETED
    assert completed.result_type == "ReservationConsumed"
    assert completed.terminal_at == received_at
    assert completed.version == 2
    assert completed.complete(
        result=DistributedOperationResult.RESERVATION_CONSUMED,
        result_message_id=result_message_id,
        reservation_id=reservation_id,
        received_at=received_at,
    ) is completed
    with pytest.raises(DistributedOperationTransitionError):
        completed.complete(
            result=DistributedOperationResult.RESERVATION_CONSUMED,
            result_message_id=uuid4(),
            reservation_id=reservation_id,
            received_at=received_at,
        )


def test_remocao_sem_reserva_e_terminal_imediato() -> None:
    item_id = uuid4()
    operation = DistributedOperation.complete_without_reservation(
        operation_id=uuid4(),
        ordem_servico_id=uuid4(),
        ordem_servico_item_id=item_id,
        resource_id=uuid4(),
        quantity=1,
        business_key=f"RELEASE:{item_id}",
        request_hash="a" * 64,
        correlation_id=uuid4(),
    )

    assert operation.state is DistributedOperationState.COMPLETED
    assert operation.terminal_result is DistributedOperationResult.NO_RESERVATION
    assert operation.command_message_id is None
    assert operation.result_message_id is None
