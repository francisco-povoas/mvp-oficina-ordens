"""Composição padrão do consumer de Ordens."""

from app.consumer import default_consumer_handlers
from app.modules.ordens_servico.application.financial_event_handler import (
    FinancialEventHandler,
)
from app.modules.ordens_servico.application.resource_result_handler import (
    ResourceResultHandler,
)


def test_composicao_padrao_ativa_saga_e_liberacao_distribuida() -> None:
    handlers = default_consumer_handlers()

    assert isinstance(handlers.resource_results, ResourceResultHandler)
    assert isinstance(handlers.financial_events, FinancialEventHandler)


def test_eventos_financeiros_possuem_handler_funcional() -> None:
    handlers = default_consumer_handlers()
    assert isinstance(handlers.financial_events, FinancialEventHandler)
