"""Coordenação persistente leve das operações distribuídas de estoque."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.modules.ordens_servico.domain.exceptions import (
    DistributedOperationIdempotencyConflictError,
    DistributedOperationInvalidError,
    DistributedOperationTransitionError,
)


class DistributedOperationType(StrEnum):
    RELEASE = "RELEASE"
    RECEIVE = "RECEIVE"
    CONSUME = "CONSUME"


class DistributedOperationState(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class DistributedOperationResult(StrEnum):
    NO_RESERVATION = "NO_RESERVATION"
    RESERVATION_RELEASED = "RESERVATION_RELEASED"
    BACKORDERED_ITEM_RECEIVED = "BACKORDERED_ITEM_RECEIVED"
    RESERVATION_CONSUMED = "RESERVATION_CONSUMED"


_COMMAND_TYPES = {
    DistributedOperationType.RELEASE: "ReleaseReservation",
    DistributedOperationType.RECEIVE: "ReceiveBackorderedItem",
    DistributedOperationType.CONSUME: "ConsumeReservation",
}
_RESULT_TYPES = {
    DistributedOperationType.RELEASE: "ReservationReleased",
    DistributedOperationType.RECEIVE: "BackorderedItemReceived",
    DistributedOperationType.CONSUME: "ReservationConsumed",
}
_TERMINAL_RESULTS = {
    DistributedOperationType.RELEASE: {
        DistributedOperationResult.NO_RESERVATION,
        DistributedOperationResult.RESERVATION_RELEASED,
    },
    DistributedOperationType.RECEIVE: {
        DistributedOperationResult.BACKORDERED_ITEM_RECEIVED,
    },
    DistributedOperationType.CONSUME: {
        DistributedOperationResult.RESERVATION_CONSUMED,
    },
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime | None, field: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise DistributedOperationInvalidError(f"{field} deve possuir timezone.")


def distributed_operation_business_key(
    operation_type: DistributedOperationType,
    *,
    ordem_servico_item_id: UUID,
    reservation_id: UUID | None = None,
) -> str:
    if operation_type is DistributedOperationType.CONSUME:
        if reservation_id is None:
            raise DistributedOperationInvalidError(
                "Consumo exige reserva para compor a chave de negócio."
            )
        identity = reservation_id
    else:
        identity = ordem_servico_item_id
    return f"{operation_type.value}:{identity}"


def distributed_operation_request_hash(
    *,
    operation_type: DistributedOperationType,
    ordem_servico_id: UUID,
    ordem_servico_item_id: UUID,
    resource_id: UUID,
    quantity: int,
    reservation_id: UUID | None,
) -> str:
    payload = {
        "operation_type": operation_type.value,
        "order_id": str(ordem_servico_id),
        "order_item_id": str(ordem_servico_item_id),
        "resource_id": str(resource_id),
        "quantity": quantity,
        "reservation_id": str(reservation_id) if reservation_id else None,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, kw_only=True)
class DistributedOperation:
    """Identifica um único efeito físico sem introduzir uma nova Saga."""

    operation_id: UUID
    operation_type: DistributedOperationType
    ordem_servico_id: UUID
    ordem_servico_item_id: UUID
    resource_id: UUID
    quantity: int
    business_key: str
    request_hash: str
    correlation_id: UUID
    state: DistributedOperationState
    command_type: str | None
    command_message_id: UUID | None
    command_created_at: datetime | None
    reservation_id: UUID | None = None
    result_type: str | None = None
    result_message_id: UUID | None = None
    result_received_at: datetime | None = None
    terminal_result: DistributedOperationResult | None = None
    terminal_at: datetime | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    version: int = 1

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise DistributedOperationInvalidError("A quantidade deve ser positiva.")
        if not self.business_key or len(self.business_key) > 220:
            raise DistributedOperationInvalidError("A chave de negócio é inválida.")
        if len(self.request_hash) != 64:
            raise DistributedOperationInvalidError("O hash da requisição deve ter 64 caracteres.")
        if self.version <= 0:
            raise DistributedOperationInvalidError("A versão deve ser positiva.")
        for field_name, value in (
            ("command_created_at", self.command_created_at),
            ("result_received_at", self.result_received_at),
            ("terminal_at", self.terminal_at),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            _require_aware(value, field_name)
        if self.state is DistributedOperationState.PENDING:
            if (
                self.command_type != _COMMAND_TYPES[self.operation_type]
                or self.command_message_id is None
                or self.command_created_at is None
                or self.terminal_result is not None
                or self.terminal_at is not None
            ):
                raise DistributedOperationInvalidError(
                    "Operação pendente exige comando correspondente e não pode ser terminal."
                )
        elif self.terminal_result not in _TERMINAL_RESULTS[self.operation_type]:
            raise DistributedOperationInvalidError(
                "Resultado terminal incompatível com o tipo da operação."
            )
        elif self.terminal_at is None:
            raise DistributedOperationInvalidError("Operação concluída exige terminal_at.")

        if self.terminal_result is DistributedOperationResult.NO_RESERVATION:
            if any(
                value is not None
                for value in (
                    self.command_type,
                    self.command_message_id,
                    self.command_created_at,
                    self.reservation_id,
                    self.result_type,
                    self.result_message_id,
                    self.result_received_at,
                )
            ):
                raise DistributedOperationInvalidError(
                    "NO_RESERVATION não pode registrar comando ou efeito físico."
                )
        elif self.state is DistributedOperationState.COMPLETED and (
            self.command_type != _COMMAND_TYPES[self.operation_type]
            or self.command_message_id is None
            or self.command_created_at is None
            or self.reservation_id is None
        ):
            raise DistributedOperationInvalidError(
                "Operação física concluída exige comando e reserva persistidos."
            )

        if self.result_type is None:
            if any(
                value is not None
                for value in (
                    self.result_message_id,
                    self.result_received_at,
                )
            ):
                raise DistributedOperationInvalidError("Metadados de resultado incompletos.")
        elif (
            self.result_type != _RESULT_TYPES[self.operation_type]
            or self.result_message_id is None
            or self.result_received_at is None
        ):
            raise DistributedOperationInvalidError("Resultado autoritativo incompatível.")

    @classmethod
    def start(
        cls,
        *,
        operation_id: UUID,
        operation_type: DistributedOperationType,
        ordem_servico_id: UUID,
        ordem_servico_item_id: UUID,
        resource_id: UUID,
        quantity: int,
        business_key: str,
        request_hash: str,
        correlation_id: UUID,
        command_message_id: UUID,
        reservation_id: UUID | None = None,
        now: datetime | None = None,
    ) -> "DistributedOperation":
        instant = now or _utc_now()
        return cls(
            operation_id=operation_id,
            operation_type=operation_type,
            ordem_servico_id=ordem_servico_id,
            ordem_servico_item_id=ordem_servico_item_id,
            resource_id=resource_id,
            quantity=quantity,
            business_key=business_key,
            request_hash=request_hash,
            correlation_id=correlation_id,
            state=DistributedOperationState.PENDING,
            command_type=_COMMAND_TYPES[operation_type],
            command_message_id=command_message_id,
            command_created_at=instant,
            reservation_id=reservation_id,
            created_at=instant,
            updated_at=instant,
        )

    @classmethod
    def complete_without_reservation(
        cls,
        *,
        operation_id: UUID,
        ordem_servico_id: UUID,
        ordem_servico_item_id: UUID,
        resource_id: UUID,
        quantity: int,
        business_key: str,
        request_hash: str,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> "DistributedOperation":
        instant = now or _utc_now()
        return cls(
            operation_id=operation_id,
            operation_type=DistributedOperationType.RELEASE,
            ordem_servico_id=ordem_servico_id,
            ordem_servico_item_id=ordem_servico_item_id,
            resource_id=resource_id,
            quantity=quantity,
            business_key=business_key,
            request_hash=request_hash,
            correlation_id=correlation_id,
            state=DistributedOperationState.COMPLETED,
            command_type=None,
            command_message_id=None,
            command_created_at=None,
            terminal_result=DistributedOperationResult.NO_RESERVATION,
            terminal_at=instant,
            created_at=instant,
            updated_at=instant,
        )

    def validate_repetition(self, request_hash: str) -> None:
        if self.request_hash != request_hash:
            raise DistributedOperationIdempotencyConflictError(
                "A mesma operação de negócio recebeu um payload diferente."
            )

    def complete(
        self,
        *,
        result: DistributedOperationResult,
        result_message_id: UUID,
        reservation_id: UUID,
        received_at: datetime,
    ) -> "DistributedOperation":
        if self.state is DistributedOperationState.COMPLETED:
            if (
                self.terminal_result is result
                and self.result_message_id == result_message_id
                and self.reservation_id == reservation_id
            ):
                return self
            raise DistributedOperationTransitionError(
                "Resultado tardio contradiz a operação já concluída."
            )
        if result not in _TERMINAL_RESULTS[self.operation_type] or result is DistributedOperationResult.NO_RESERVATION:
            raise DistributedOperationTransitionError(
                "Resultado autoritativo incompatível com a operação pendente."
            )
        _require_aware(received_at, "received_at")
        return replace(
            self,
            state=DistributedOperationState.COMPLETED,
            reservation_id=reservation_id,
            result_type=_RESULT_TYPES[self.operation_type],
            result_message_id=result_message_id,
            result_received_at=received_at,
            terminal_result=result,
            terminal_at=received_at,
            updated_at=received_at,
            version=self.version + 1,
        )
