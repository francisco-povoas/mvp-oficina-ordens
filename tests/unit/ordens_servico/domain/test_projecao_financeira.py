"""Testes da projeção financeira mínima mantida por Ordens."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoPagamentoNaoRegistradoError,
)
from app.shared.value_objects.id import ID


def _ordem_finalizada() -> OrdemServico:
    agora = datetime.now(UTC)
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=StatusOrdemServico.FINALIZADA,
        queixa_inicial="Ruído no motor",
        diagnostico="Correia desgastada",
        criado_em=agora,
        atualizado_em=agora,
        iniciado_diagnostico_em=agora,
        diagnostico_concluido_em=agora,
    )


def test_entrega_exige_projecao_confirmada() -> None:
    with pytest.raises(OrdemServicoPagamentoNaoRegistradoError):
        _ordem_finalizada().entregar()


def test_projecao_confirmada_habilita_entrega_sem_incorporar_agregado() -> None:
    ordem = _ordem_finalizada()
    payment_id = uuid4()
    quote_id = uuid4()
    confirmado_em = datetime.now(UTC)

    projetada = ordem.com_pagamento_confirmado(
        payment_id=payment_id,
        quote_id=quote_id,
        amount=Decimal("270.00"),
        confirmed_at=confirmado_em,
        quote_version=2,
    )
    entregue = projetada.entregar()

    assert entregue.status is StatusOrdemServico.ENTREGUE
    assert entregue.payment_id == payment_id
    assert entregue.payment_status == "CONFIRMED"
    assert entregue.payment_quote_id == quote_id
    assert entregue.payment_quote_version == 2
    assert entregue.version == ordem.version + 2


@pytest.mark.parametrize(
    ("amount", "quote_version"),
    [(Decimal("0"), 1), (Decimal("-1"), 1), (Decimal("1"), 0)],
)
def test_rejeita_projecao_invalida(amount: Decimal, quote_version: int) -> None:
    with pytest.raises(OrdemServicoInvalidaError):
        _ordem_finalizada().com_pagamento_confirmado(
            payment_id=uuid4(),
            quote_id=uuid4(),
            amount=amount,
            confirmed_at=datetime.now(UTC),
            quote_version=quote_version,
        )
