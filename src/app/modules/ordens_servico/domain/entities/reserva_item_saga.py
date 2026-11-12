"""Estado persistente da Saga de inclusão e reserva de uma peça."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.modules.ordens_servico.domain.exceptions import (
    ReservaItemSagaInvalidaError,
    ReservaItemSagaTransicaoInvalidaError,
)


class EstadoReservaItemSaga(StrEnum):
    PENDING = "PENDING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    RELEASING = "RELEASING"
    COMPLETED = "COMPLETED"
    COMPENSATED = "COMPENSATED"
    REJECTED = "REJECTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ComandoReservaItemSaga(StrEnum):
    RESERVE_ITEM = "RESERVE_ITEM"
    RELEASE_RESERVATION = "RELEASE_RESERVATION"


class ResultadoTerminalReservaItemSaga(StrEnum):
    RESERVED = "RESERVED"
    BACKORDERED = "BACKORDERED"
    NO_RESERVATION = "NO_RESERVATION"
    RESERVATION_RELEASED = "RESERVATION_RELEASED"
    RESERVATION_REJECTED = "RESERVATION_REJECTED"


class CodigoRejeicaoReservaItemSaga(StrEnum):
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INVALID_QUANTITY = "INVALID_QUANTITY"


_ESTADOS_TERMINAIS = {
    EstadoReservaItemSaga.COMPLETED,
    EstadoReservaItemSaga.COMPENSATED,
    EstadoReservaItemSaga.REJECTED,
}


@dataclass(frozen=True, kw_only=True)
class ReservaItemSaga:
    """Coordenação específica da reserva, sem carregar regras de saldo de estoque."""

    id: UUID
    operation_id: UUID
    ordem_servico_id: UUID
    ordem_servico_item_id: UUID
    resource_id: UUID
    quantity: int
    correlation_id: UUID
    idempotency_key: str
    request_hash: str
    estado: EstadoReservaItemSaga
    comando_pendente: ComandoReservaItemSaga | None
    ultimo_comando_message_id: UUID | None
    terminal_result: ResultadoTerminalReservaItemSaga | None
    reservation_id: UUID | None
    rejection_code: CodigoRejeicaoReservaItemSaga | None
    rejection_detail: str | None
    ultimo_resultado_type: str | None
    ultimo_resultado_message_id: UUID | None
    ultimo_resultado_em: datetime | None
    cancellation_requested_at: datetime | None
    terminal_at: datetime | None
    resource_name: str | None
    resource_description: str | None
    resource_unit_price: Decimal | None
    criado_em: datetime
    atualizado_em: datetime
    version: int = 1

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ReservaItemSagaInvalidaError("A quantidade da Saga deve ser positiva.")
        if not 8 <= len(self.idempotency_key) <= 128:
            raise ReservaItemSagaInvalidaError(
                "A chave de idempotência deve possuir entre 8 e 128 caracteres."
            )
        if len(self.request_hash) != 64:
            raise ReservaItemSagaInvalidaError("O hash da requisição deve ser SHA-256.")
        if self.version < 1:
            raise ReservaItemSagaInvalidaError("A versão da Saga deve ser positiva.")
        if self.resource_unit_price is not None and self.resource_unit_price < Decimal("0"):
            raise ReservaItemSagaInvalidaError("O valor do snapshot não pode ser negativo.")
        if self.estado in _ESTADOS_TERMINAIS:
            if self.terminal_result is None or self.terminal_at is None:
                raise ReservaItemSagaInvalidaError(
                    "Saga terminal exige resultado e instante terminais."
                )
            if self.comando_pendente is not None:
                raise ReservaItemSagaInvalidaError("Saga terminal não pode ter comando pendente.")
        elif self.terminal_result is not None or self.terminal_at is not None:
            raise ReservaItemSagaInvalidaError(
                "Saga não terminal não pode possuir resultado terminal."
            )

    @classmethod
    def iniciar(
        cls,
        *,
        saga_id: UUID,
        operation_id: UUID,
        ordem_servico_id: UUID,
        ordem_servico_item_id: UUID,
        resource_id: UUID,
        quantity: int,
        correlation_id: UUID,
        idempotency_key: str,
        request_hash: str,
        reserve_message_id: UUID,
        agora: datetime | None = None,
    ) -> "ReservaItemSaga":
        instante = agora or datetime.now(UTC)
        return cls(
            id=saga_id,
            operation_id=operation_id,
            ordem_servico_id=ordem_servico_id,
            ordem_servico_item_id=ordem_servico_item_id,
            resource_id=resource_id,
            quantity=quantity,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            estado=EstadoReservaItemSaga.PENDING,
            comando_pendente=ComandoReservaItemSaga.RESERVE_ITEM,
            ultimo_comando_message_id=reserve_message_id,
            terminal_result=None,
            reservation_id=None,
            rejection_code=None,
            rejection_detail=None,
            ultimo_resultado_type=None,
            ultimo_resultado_message_id=None,
            ultimo_resultado_em=None,
            cancellation_requested_at=None,
            terminal_at=None,
            resource_name=None,
            resource_description=None,
            resource_unit_price=None,
            criado_em=instante,
            atualizado_em=instante,
        )

    @property
    def terminal(self) -> bool:
        return self.estado in _ESTADOS_TERMINAIS

    def solicitar_cancelamento(self, *, agora: datetime | None = None) -> "ReservaItemSaga":
        if self.estado in {EstadoReservaItemSaga.CANCEL_REQUESTED, EstadoReservaItemSaga.RELEASING}:
            return self
        if self.estado in {
            EstadoReservaItemSaga.COMPENSATED,
            EstadoReservaItemSaga.REJECTED,
        }:
            return self
        if self.estado not in {
            EstadoReservaItemSaga.PENDING,
            EstadoReservaItemSaga.RECONCILIATION_REQUIRED,
        }:
            raise ReservaItemSagaTransicaoInvalidaError(
                f"Cancelamento pendente não é permitido em '{self.estado.value}'."
            )
        instante = agora or datetime.now(UTC)
        return replace(
            self,
            estado=EstadoReservaItemSaga.CANCEL_REQUESTED,
            cancellation_requested_at=instante,
            atualizado_em=instante,
            version=self.version + 1,
        )

    def registrar_reserva(
        self,
        *,
        reservation_id: UUID,
        message_id: UUID,
        resultado_em: datetime,
        resource_name: str,
        resource_description: str | None,
        resource_unit_price: Decimal,
        release_message_id: UUID | None = None,
    ) -> "ReservaItemSaga":
        if self.estado is EstadoReservaItemSaga.COMPLETED:
            if (
                self.terminal_result is ResultadoTerminalReservaItemSaga.RESERVED
                and self.reservation_id == reservation_id
            ):
                return self
            self._transicao_invalida("ResourceReserved")
        if self.estado not in {
            EstadoReservaItemSaga.PENDING,
            EstadoReservaItemSaga.CANCEL_REQUESTED,
        }:
            self._transicao_invalida("ResourceReserved")
        comum = dict(
            reservation_id=reservation_id,
            ultimo_resultado_type="ResourceReserved",
            ultimo_resultado_message_id=message_id,
            ultimo_resultado_em=resultado_em,
            resource_name=resource_name,
            resource_description=resource_description,
            resource_unit_price=resource_unit_price,
            atualizado_em=resultado_em,
            version=self.version + 1,
        )
        if self.estado is EstadoReservaItemSaga.CANCEL_REQUESTED:
            if release_message_id is None:
                raise ReservaItemSagaInvalidaError(
                    "Reserva tardia após cancelamento exige comando de liberação."
                )
            return replace(
                self,
                estado=EstadoReservaItemSaga.RELEASING,
                comando_pendente=ComandoReservaItemSaga.RELEASE_RESERVATION,
                ultimo_comando_message_id=release_message_id,
                **comum,
            )
        return replace(
            self,
            estado=EstadoReservaItemSaga.COMPLETED,
            comando_pendente=None,
            terminal_result=ResultadoTerminalReservaItemSaga.RESERVED,
            terminal_at=resultado_em,
            **comum,
        )

    def registrar_backorder(
        self,
        *,
        message_id: UUID,
        resultado_em: datetime,
        resource_name: str,
        resource_description: str | None,
        resource_unit_price: Decimal,
    ) -> "ReservaItemSaga":
        if self.terminal:
            esperado = (
                self.estado is EstadoReservaItemSaga.COMPLETED
                and self.terminal_result is ResultadoTerminalReservaItemSaga.BACKORDERED
            ) or (
                self.estado is EstadoReservaItemSaga.COMPENSATED
                and self.terminal_result is ResultadoTerminalReservaItemSaga.NO_RESERVATION
            )
            if esperado:
                return self
            self._transicao_invalida("ItemBackordered")
        if self.estado not in {
            EstadoReservaItemSaga.PENDING,
            EstadoReservaItemSaga.CANCEL_REQUESTED,
        }:
            self._transicao_invalida("ItemBackordered")
        cancelada = self.estado is EstadoReservaItemSaga.CANCEL_REQUESTED
        return replace(
            self,
            estado=(
                EstadoReservaItemSaga.COMPENSATED
                if cancelada
                else EstadoReservaItemSaga.COMPLETED
            ),
            comando_pendente=None,
            terminal_result=(
                ResultadoTerminalReservaItemSaga.NO_RESERVATION
                if cancelada
                else ResultadoTerminalReservaItemSaga.BACKORDERED
            ),
            ultimo_resultado_type="ItemBackordered",
            ultimo_resultado_message_id=message_id,
            ultimo_resultado_em=resultado_em,
            resource_name=resource_name,
            resource_description=resource_description,
            resource_unit_price=resource_unit_price,
            terminal_at=resultado_em,
            atualizado_em=resultado_em,
            version=self.version + 1,
        )

    def registrar_rejeicao(
        self,
        *,
        code: CodigoRejeicaoReservaItemSaga,
        detail: str,
        message_id: UUID,
        resultado_em: datetime,
    ) -> "ReservaItemSaga":
        if self.estado is EstadoReservaItemSaga.REJECTED:
            if self.rejection_code is code:
                return self
            self._transicao_invalida("ReservationRejected")
        if self.estado not in {
            EstadoReservaItemSaga.PENDING,
            EstadoReservaItemSaga.CANCEL_REQUESTED,
        }:
            self._transicao_invalida("ReservationRejected")
        return replace(
            self,
            estado=EstadoReservaItemSaga.REJECTED,
            comando_pendente=None,
            terminal_result=ResultadoTerminalReservaItemSaga.RESERVATION_REJECTED,
            rejection_code=code,
            rejection_detail=detail,
            ultimo_resultado_type="ReservationRejected",
            ultimo_resultado_message_id=message_id,
            ultimo_resultado_em=resultado_em,
            terminal_at=resultado_em,
            atualizado_em=resultado_em,
            version=self.version + 1,
        )

    def registrar_liberacao(
        self,
        *,
        reservation_id: UUID,
        message_id: UUID,
        resultado_em: datetime,
    ) -> "ReservaItemSaga":
        if self.estado is EstadoReservaItemSaga.COMPENSATED:
            if (
                self.terminal_result is ResultadoTerminalReservaItemSaga.RESERVATION_RELEASED
                and self.reservation_id == reservation_id
            ):
                return self
            self._transicao_invalida("ReservationReleased")
        if self.estado is not EstadoReservaItemSaga.RELEASING:
            self._transicao_invalida("ReservationReleased")
        if self.reservation_id != reservation_id:
            raise ReservaItemSagaTransicaoInvalidaError(
                "A liberação não pertence à reserva persistida na Saga."
            )
        return replace(
            self,
            estado=EstadoReservaItemSaga.COMPENSATED,
            comando_pendente=None,
            terminal_result=ResultadoTerminalReservaItemSaga.RESERVATION_RELEASED,
            ultimo_resultado_type="ReservationReleased",
            ultimo_resultado_message_id=message_id,
            ultimo_resultado_em=resultado_em,
            terminal_at=resultado_em,
            atualizado_em=resultado_em,
            version=self.version + 1,
        )

    def exigir_reconciliacao(self, *, agora: datetime | None = None) -> "ReservaItemSaga":
        if self.terminal:
            self._transicao_invalida("RECONCILIATION_REQUIRED")
        if self.estado is EstadoReservaItemSaga.RECONCILIATION_REQUIRED:
            return self
        instante = agora or datetime.now(UTC)
        return replace(
            self,
            estado=EstadoReservaItemSaga.RECONCILIATION_REQUIRED,
            atualizado_em=instante,
            version=self.version + 1,
        )

    def _transicao_invalida(self, resultado: str) -> None:
        raise ReservaItemSagaTransicaoInvalidaError(
            f"'{resultado}' não é aplicável à Saga em '{self.estado.value}'."
        )
