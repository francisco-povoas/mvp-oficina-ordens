"""Composição padrão do consumer de Ordens."""

from unittest.mock import MagicMock

import pytest

from app.consumer import default_consumer_handlers
from app.modules.ordens_servico.application.resource_result_handler import (
    ResourceResultHandler,
)
from app.modules.ordens_servico.domain.exceptions import (
    DistributedOperationInvalidError,
)


def test_composicao_padrao_ativa_saga_e_liberacao_distribuida() -> None:
    handlers = default_consumer_handlers()

    assert isinstance(handlers.resource_results, ResourceResultHandler)
    assert handlers.financial_events is None


@pytest.mark.parametrize("message_type", ["ReservationConsumed"])
async def test_resultado_fora_da_saga_permanece_fail_closed(
    message_type: str,
) -> None:
    handler = default_consumer_handlers().resource_results
    assert handler is not None

    with pytest.raises(DistributedOperationInvalidError, match="não suportado"):
        await handler.handle(
            {"message_type": message_type},
            MagicMock(),
        )
