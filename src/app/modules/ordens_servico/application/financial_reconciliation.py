"""Comparação explícita entre a projeção local e Financeiro."""

from dataclasses import dataclass
from enum import StrEnum

from app.integrations.finance import FinancialStatus
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrcamentoFinanceiro,
)


class FinancialReconciliationCategory(StrEnum):
    SYNCHRONIZED = "SYNCHRONIZED"
    UPDATE_REQUIRED = "UPDATE_REQUIRED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    INCONSISTENT = "INCONSISTENT"


@dataclass(frozen=True, slots=True)
class FinancialReconciliationComparison:
    category: FinancialReconciliationCategory
    reason: str


def temporarily_unavailable(reason: str) -> FinancialReconciliationComparison:
    return FinancialReconciliationComparison(
        FinancialReconciliationCategory.TEMPORARILY_UNAVAILABLE,
        reason,
    )


def compare_financial_projection(
    order: OrdemServico,
    snapshot: FinancialStatus | None,
) -> FinancialReconciliationComparison:
    """Classifica sem inferir decisões ausentes ou regressivas."""

    if snapshot is None:
        if order.quote_id is None and order.payment_id is None:
            return _result(FinancialReconciliationCategory.SYNCHRONIZED, "no_financial_facts")
        return _result(
            FinancialReconciliationCategory.INCONSISTENT,
            "authoritative_status_missing_for_local_projection",
        )
    if snapshot.order_id != order.id.value:
        return _result(FinancialReconciliationCategory.INCONSISTENT, "order_identity_mismatch")

    quote = snapshot.quote
    payment = snapshot.payment
    if quote is None:
        if payment is not None or order.quote_id is not None or order.payment_id is not None:
            return _result(FinancialReconciliationCategory.INCONSISTENT, "quote_identity_missing")
        return _result(FinancialReconciliationCategory.SYNCHRONIZED, "no_financial_facts")
    if quote.status not in {"RASCUNHO", "COMUNICADO", "APROVADO", "RECUSADO"}:
        return _result(FinancialReconciliationCategory.INCONSISTENT, "unknown_quote_status")
    if order.quote_id is not None and order.quote_id != quote.quote_id:
        return _result(FinancialReconciliationCategory.INCONSISTENT, "quote_identity_mismatch")
    if order.quote_version is not None and order.quote_version > quote.quote_version:
        return _result(FinancialReconciliationCategory.INCONSISTENT, "local_quote_version_ahead")

    if payment is not None:
        if payment.quote_id != quote.quote_id or payment.quote_version != quote.quote_version:
            return _result(FinancialReconciliationCategory.INCONSISTENT, "payment_quote_mismatch")
        if payment.status == "CONFIRMED":
            if payment.confirmed_at is None or quote.status != "APROVADO":
                return _result(
                    FinancialReconciliationCategory.INCONSISTENT,
                    "confirmed_payment_without_approved_quote",
                )
            if payment.amount < quote.total:
                return _result(
                    FinancialReconciliationCategory.INCONSISTENT,
                    "confirmed_payment_below_quote_total",
                )
        elif payment.confirmed_at is not None:
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "unconfirmed_payment_has_confirmation_time",
            )
    if order.payment_id is not None:
        if payment is None or payment.status != "CONFIRMED":
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "local_confirmation_not_supported_by_authority",
            )
        if (
            order.payment_id != payment.payment_id
            or order.payment_quote_id != payment.quote_id
            or order.payment_quote_version != payment.quote_version
            or order.payment_amount != payment.amount
            or order.payment_confirmed_at != payment.confirmed_at
        ):
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "confirmed_payment_projection_mismatch",
            )

    actionable_status = {
        "COMUNICADO": StatusOrcamentoFinanceiro.COMUNICADO,
        "APROVADO": StatusOrcamentoFinanceiro.APROVADO,
        "RECUSADO": StatusOrcamentoFinanceiro.RECUSADO,
    }.get(quote.status)
    if actionable_status is None:
        if order.quote_id is not None:
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "draft_quote_conflicts_with_local_projection",
            )
    elif order.quote_version == quote.quote_version:
        if order.quote_status is not actionable_status:
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "same_quote_version_has_different_status",
            )
        if quote.status == "COMUNICADO" and (
            quote.communicated_at is None
            or order.quote_total != quote.total
            or order.quote_communicated_at != quote.communicated_at
        ):
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "communicated_quote_projection_mismatch",
            )
        if quote.status in {"APROVADO", "RECUSADO"} and (
            quote.decided_at is None or order.quote_decided_at != quote.decided_at
        ):
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "quote_decision_projection_mismatch",
            )
        if (
            quote.status == "RECUSADO"
            and order.quote_rejection_reason != quote.rejection_reason
        ):
            return _result(
                FinancialReconciliationCategory.INCONSISTENT,
                "quote_rejection_reason_mismatch",
            )

    quote_needs_update = actionable_status is not None and (
        order.quote_version is None or order.quote_version < quote.quote_version
    )
    payment_needs_update = (
        payment is not None
        and payment.status == "CONFIRMED"
        and order.payment_id is None
    )
    if quote_needs_update or payment_needs_update:
        return _result(FinancialReconciliationCategory.UPDATE_REQUIRED, "authoritative_fact_missing")
    return _result(FinancialReconciliationCategory.SYNCHRONIZED, "projection_matches_authority")


def _result(
    category: FinancialReconciliationCategory,
    reason: str,
) -> FinancialReconciliationComparison:
    return FinancialReconciliationComparison(category, reason)
