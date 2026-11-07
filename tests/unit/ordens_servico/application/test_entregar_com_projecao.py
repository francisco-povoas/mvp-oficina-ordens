"""Entrega baseada na projeção financeira confirmada."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.ordens_servico.application.use_cases.entregar_ordem_servico import (
    EntregarOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.shared.value_objects.id import ID


def _ordem_liquidada() -> OrdemServico:
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
        payment_id=uuid4(),
        payment_status="CONFIRMED",
        payment_amount=Decimal("270.00"),
        payment_confirmed_at=agora,
        payment_quote_version=1,
    )


async def test_entrega_sem_itens_persiste_e_confirma_transacao() -> None:
    ordem = _ordem_liquidada()
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=ordem)
    repo.listar_itens_da_os = AsyncMock(return_value=[])
    repo.listar_servicos_da_os = AsyncMock(return_value=[])
    repo.atualizar = AsyncMock()

    uow = MagicMock()
    uow.ordem_servico_repo = repo
    uow.item_estoque_repo = MagicMock()
    uow.commit = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    resposta = await EntregarOrdemServicoUseCase(uow=uow).execute(str(ordem.id))

    assert resposta.status == StatusOrdemServico.ENTREGUE.value
    assert resposta.payment_id == str(ordem.payment_id)
    repo.atualizar.assert_awaited_once()
    uow.commit.assert_awaited_once()
