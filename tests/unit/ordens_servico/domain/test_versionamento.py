"""Invariantes de versão e preservação de metadados da OS."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.shared.value_objects.id import ID


def _ordem_com_metadados() -> OrdemServico:
    agora = datetime.now(UTC)
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=StatusOrdemServico.RECEBIDA,
        queixa_inicial="Ruído",
        diagnostico=None,
        criado_em=agora,
        atualizado_em=agora,
        iniciado_diagnostico_em=None,
        diagnostico_concluido_em=None,
        version=9,
        payment_id=uuid4(),
        payment_status="CONFIRMED",
        payment_amount=Decimal("120.00"),
        payment_confirmed_at=agora,
        payment_quote_version=4,
    )


def test_fluxo_de_diagnostico_preserva_metadados_e_incrementa_cada_mutacao() -> None:
    original = _ordem_com_metadados()

    iniciada = original.iniciar_diagnostico()
    registrada = iniciada.registrar_diagnostico("Correia desgastada")
    concluida = registrada.concluir_diagnostico()

    assert [iniciada.version, registrada.version, concluida.version] == [10, 11, 12]
    assert concluida.payment_id == original.payment_id
    assert concluida.payment_status == original.payment_status
    assert concluida.payment_amount == original.payment_amount
    assert concluida.payment_confirmed_at == original.payment_confirmed_at
    assert concluida.payment_quote_version == original.payment_quote_version
    assert concluida.cliente_id == original.cliente_id
    assert concluida.veiculo_id == original.veiculo_id


def test_alteracao_em_filho_avanca_versao_sem_reconstruir_entidade() -> None:
    original = _ordem_com_metadados()
    atualizada = original.registrar_alteracao()

    assert atualizada.version == 10
    assert atualizada.payment_id == original.payment_id
    assert atualizada.criado_em == original.criado_em
