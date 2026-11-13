"""Transições do estado interno da Saga de reserva."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    CodigoRejeicaoReservaItemSaga,
    ComandoReservaItemSaga,
    EstadoReservaItemSaga,
    ReservaItemSaga,
    ResultadoTerminalReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ReservaItemSagaInvalidaError,
    ReservaItemSagaTransicaoInvalidaError,
)


def _saga() -> ReservaItemSaga:
    return ReservaItemSaga.iniciar(
        saga_id=uuid4(),
        operation_id=uuid4(),
        ordem_servico_id=uuid4(),
        ordem_servico_item_id=uuid4(),
        resource_id=uuid4(),
        quantity=2,
        correlation_id=uuid4(),
        idempotency_key="request-123",
        request_hash="a" * 64,
        reserve_message_id=uuid4(),
    )


def test_inicia_pendente_com_comando_e_identidades_persistentes() -> None:
    saga = _saga()

    assert saga.estado is EstadoReservaItemSaga.PENDING
    assert saga.comando_pendente is ComandoReservaItemSaga.RESERVE_ITEM
    assert saga.terminal_result is None
    assert saga.version == 1
    assert not saga.terminal


def test_reserva_conclui_e_repeticao_identica_e_idempotente() -> None:
    saga = _saga()
    reservation_id = uuid4()
    instante = datetime.now(UTC)
    concluida = saga.registrar_reserva(
        reservation_id=reservation_id,
        message_id=uuid4(),
        resultado_em=instante,
        resource_name="Filtro",
        resource_description=None,
        resource_unit_price=Decimal("45.90"),
    )

    assert concluida.estado is EstadoReservaItemSaga.COMPLETED
    assert concluida.terminal_result is ResultadoTerminalReservaItemSaga.RESERVED
    assert concluida.reservation_id == reservation_id
    assert concluida.terminal_at == instante
    assert concluida.registrar_reserva(
        reservation_id=reservation_id,
        message_id=uuid4(),
        resultado_em=instante + timedelta(seconds=1),
        resource_name="Filtro",
        resource_description=None,
        resource_unit_price=Decimal("45.90"),
    ) is concluida


def test_backorder_conclui_sem_reserva() -> None:
    instante = datetime.now(UTC)
    concluida = _saga().registrar_backorder(
        message_id=uuid4(),
        resultado_em=instante,
        resource_name="Pastilha",
        resource_description="Jogo",
        resource_unit_price=Decimal("90.00"),
    )

    assert concluida.estado is EstadoReservaItemSaga.COMPLETED
    assert concluida.terminal_result is ResultadoTerminalReservaItemSaga.BACKORDERED
    assert concluida.reservation_id is None


@pytest.mark.parametrize(
    "code",
    [
        CodigoRejeicaoReservaItemSaga.RESOURCE_NOT_FOUND,
        CodigoRejeicaoReservaItemSaga.INVALID_QUANTITY,
    ],
)
def test_rejeicao_de_negocio_e_terminal(code: CodigoRejeicaoReservaItemSaga) -> None:
    rejeitada = _saga().registrar_rejeicao(
        code=code,
        detail="Rejeição segura",
        message_id=uuid4(),
        resultado_em=datetime.now(UTC),
    )

    assert rejeitada.estado is EstadoReservaItemSaga.REJECTED
    assert rejeitada.terminal_result is ResultadoTerminalReservaItemSaga.RESERVATION_REJECTED
    assert rejeitada.rejection_code is code


def test_cancelamento_antes_da_reserva_exige_liberacao_e_conclui_compensacao() -> None:
    cancelada = _saga().solicitar_cancelamento()
    reservation_id = uuid4()
    reservada = cancelada.registrar_reserva(
        reservation_id=reservation_id,
        message_id=uuid4(),
        resultado_em=datetime.now(UTC),
        resource_name="Filtro",
        resource_description=None,
        resource_unit_price=Decimal("10.00"),
        release_message_id=uuid4(),
    )

    assert reservada.estado is EstadoReservaItemSaga.RELEASING
    assert reservada.comando_pendente is ComandoReservaItemSaga.RELEASE_RESERVATION

    compensada = reservada.registrar_liberacao(
        reservation_id=reservation_id,
        message_id=uuid4(),
        resultado_em=datetime.now(UTC),
    )
    assert compensada.estado is EstadoReservaItemSaga.COMPENSATED
    assert (
        compensada.terminal_result
        is ResultadoTerminalReservaItemSaga.RESERVATION_RELEASED
    )


def test_cancelamento_com_backorder_termina_sem_liberacao() -> None:
    compensada = _saga().solicitar_cancelamento().registrar_backorder(
        message_id=uuid4(),
        resultado_em=datetime.now(UTC),
        resource_name="Filtro",
        resource_description=None,
        resource_unit_price=Decimal("10.00"),
    )

    assert compensada.estado is EstadoReservaItemSaga.COMPENSATED
    assert compensada.terminal_result is ResultadoTerminalReservaItemSaga.NO_RESERVATION
    assert compensada.comando_pendente is None


def test_resultado_contraditorio_nao_reabre_terminal() -> None:
    concluida = _saga().registrar_backorder(
        message_id=uuid4(),
        resultado_em=datetime.now(UTC),
        resource_name="Filtro",
        resource_description=None,
        resource_unit_price=Decimal("10.00"),
    )

    with pytest.raises(ReservaItemSagaTransicaoInvalidaError):
        concluida.registrar_reserva(
            reservation_id=uuid4(),
            message_id=uuid4(),
            resultado_em=datetime.now(UTC),
            resource_name="Filtro",
            resource_description=None,
            resource_unit_price=Decimal("10.00"),
        )


def test_reconciliacao_nao_converte_falha_tecnica_em_rejeicao() -> None:
    reconciliacao = _saga().exigir_reconciliacao()

    assert reconciliacao.estado is EstadoReservaItemSaga.RECONCILIATION_REQUIRED
    assert reconciliacao.terminal_result is None
    assert reconciliacao.rejection_code is None


@pytest.mark.parametrize(
    ("key", "request_hash", "quantity"),
    [("curta", "a" * 64, 1), ("request-123", "invalido", 1), ("request-123", "a" * 64, 0)],
)
def test_rejeita_identidade_ou_quantidade_invalida(
    key: str,
    request_hash: str,
    quantity: int,
) -> None:
    with pytest.raises(ReservaItemSagaInvalidaError):
        ReservaItemSaga.iniciar(
            saga_id=uuid4(),
            operation_id=uuid4(),
            ordem_servico_id=uuid4(),
            ordem_servico_item_id=uuid4(),
            resource_id=uuid4(),
            quantity=quantity,
            correlation_id=uuid4(),
            idempotency_key=key,
            request_hash=request_hash,
            reserve_message_id=uuid4(),
        )
