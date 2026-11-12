from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.integrations.finance import (
    FinancialStatus,
    PaymentFinancialStatus,
    QuoteFinancialStatus,
)
from app.modules.ordens_servico.application.financial_reconciliation import (
    FinancialReconciliationCategory,
    compare_financial_projection,
    temporarily_unavailable,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrcamentoFinanceiro,
    StatusOrdemServico,
)
from app.shared.value_objects.id import ID


def _order(**changes) -> OrdemServico:
    now = datetime.now(UTC)
    order = OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=StatusOrdemServico.DIAGNOSTICO_CONCLUIDO,
        queixa_inicial="Ruído",
        diagnostico="Correia",
        criado_em=now,
        atualizado_em=now,
        iniciado_diagnostico_em=now,
        diagnostico_concluido_em=now,
    )
    return replace(order, **changes)


def _snapshot(order, *, status="APROVADO", payment_status=None) -> FinancialStatus:
    now = datetime.now(UTC)
    quote_id = uuid4()
    payment = None
    if payment_status:
        payment = PaymentFinancialStatus(
            payment_id=uuid4(),
            quote_id=quote_id,
            quote_version=3,
            attempt_number=1,
            payment_version=2,
            status=payment_status,
            amount=Decimal("150.00"),
            confirmed_at=now if payment_status == "CONFIRMED" else None,
        )
    return FinancialStatus(
        order_id=order.id.value,
        quote=QuoteFinancialStatus(
            quote_id=quote_id,
            quote_version=3,
            status=status,
            total=Decimal("150.00"),
            communicated_at=now,
            decided_at=now if status in {"APROVADO", "RECUSADO"} else None,
            rejection_reason="Cliente recusou" if status == "RECUSADO" else None,
        ),
        payment=payment,
        reconciled_at=now,
    )


def test_classifica_ausencia_sincronizada_e_indisponibilidade() -> None:
    order = _order()
    assert compare_financial_projection(order, None).category is FinancialReconciliationCategory.SYNCHRONIZED
    assert temporarily_unavailable("timeout").category is FinancialReconciliationCategory.TEMPORARILY_UNAVAILABLE


def test_classifica_aprovacao_e_pagamento_ausentes_como_atualizacao() -> None:
    order = _order(status=StatusOrdemServico.FINALIZADA)
    comparison = compare_financial_projection(
        order,
        _snapshot(order, payment_status="CONFIRMED"),
    )
    assert comparison.category is FinancialReconciliationCategory.UPDATE_REQUIRED


def test_unknown_nao_cria_atualizacao_ficticia() -> None:
    order = _order()
    comparison = compare_financial_projection(
        order,
        _snapshot(order, payment_status="UNKNOWN"),
    )
    assert comparison.category is FinancialReconciliationCategory.UPDATE_REQUIRED
    assert comparison.reason == "authoritative_fact_missing"


def test_projecao_equivalente_esta_sincronizada() -> None:
    order = _order(status=StatusOrdemServico.FINALIZADA)
    snapshot = _snapshot(order, payment_status="CONFIRMED")
    quote = snapshot.quote
    payment = snapshot.payment
    projected = replace(
        order,
        quote_id=quote.quote_id,
        quote_status=StatusOrcamentoFinanceiro.APROVADO,
        quote_version=quote.quote_version,
        quote_decided_at=quote.decided_at,
        payment_id=payment.payment_id,
        payment_status="CONFIRMED",
        payment_amount=payment.amount,
        payment_confirmed_at=payment.confirmed_at,
        payment_quote_id=payment.quote_id,
        payment_quote_version=payment.quote_version,
    )
    assert compare_financial_projection(projected, snapshot).category is FinancialReconciliationCategory.SYNCHRONIZED


def test_identidade_versao_e_confirmacao_divergentes_exigem_investigacao() -> None:
    order = _order(quote_id=uuid4(), quote_version=4)
    snapshot = _snapshot(order)
    assert compare_financial_projection(order, snapshot).category is FinancialReconciliationCategory.INCONSISTENT

    snapshot = replace(
        snapshot,
        payment=PaymentFinancialStatus(
            payment_id=uuid4(),
            quote_id=uuid4(),
            quote_version=3,
            attempt_number=1,
            payment_version=1,
            status="CONFIRMED",
            amount=Decimal("150.00"),
            confirmed_at=datetime.now(UTC),
        ),
    )
    assert compare_financial_projection(_order(), snapshot).category is FinancialReconciliationCategory.INCONSISTENT
