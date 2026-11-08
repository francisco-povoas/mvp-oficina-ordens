"""Entrega baseada na projeção financeira confirmada."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.integrations.finance import (
    FinancialStatus,
    PaymentFinancialStatus,
    QuoteFinancialStatus,
)
from app.integrations.resources import (
    ConsumirReservaResult,
    ResourceDependencyUnavailable,
)
from app.modules.ordens_servico.application.use_cases.entregar_ordem_servico import (
    EntregarOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoPagamentoNaoRegistradoError,
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
    uow.commit = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    recursos = MagicMock()
    resposta = await EntregarOrdemServicoUseCase(
        uow=uow, recursos=recursos
    ).execute(str(ordem.id))

    assert resposta.status == StatusOrdemServico.ENTREGUE.value
    assert resposta.payment_id == str(ordem.payment_id)
    repo.atualizar.assert_awaited_once()
    uow.commit.assert_awaited_once()


def _item_em_uso(ordem: OrdemServico) -> OrdemServicoItem:
    return OrdemServicoItem(
        id=ID.generate(),
        ordem_servico_id=ordem.id.value,
        item_estoque_id=uuid4(),
        nome_item="Correia",
        tipo_item="PECA",
        quantidade=1,
        valor_unitario=Decimal("80.00"),
        status=StatusItemNaOS.EM_USO,
    )


def _uow_entrega(ordem: OrdemServico, item: OrdemServicoItem):
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=ordem)
    repo.listar_itens_da_os = AsyncMock(return_value=[item])
    repo.listar_servicos_da_os = AsyncMock(return_value=[])
    repo.atualizar_item = AsyncMock()
    repo.atualizar = AsyncMock()
    uow = MagicMock()
    uow.ordem_servico_repo = repo
    uow.commit = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    return repo, uow


async def test_entrega_consolida_consumo_confirmado_por_recursos() -> None:
    ordem = _ordem_liquidada()
    item = _item_em_uso(ordem)
    repo, uow = _uow_entrega(ordem, item)
    recursos = MagicMock()
    recursos.consumir_reserva = AsyncMock(
        side_effect=lambda command: ConsumirReservaResult(
            operation_id=command.operation_id,
            recurso_id=command.recurso_id,
            quantidade=command.quantidade,
            reservation_id=uuid4(),
            consumido_em=datetime.now(UTC),
        )
    )

    resposta = await EntregarOrdemServicoUseCase(
        uow=uow, recursos=recursos
    ).execute(str(ordem.id))

    assert resposta.itens[0].status == StatusItemNaOS.CONSUMIDO.value
    repo.atualizar_item.assert_awaited_once()
    assert repo.atualizar.await_args.args[0].version == ordem.version + 1


async def test_entrega_nao_marca_consumo_quando_recursos_esta_indisponivel() -> None:
    ordem = _ordem_liquidada()
    item = _item_em_uso(ordem)
    repo, uow = _uow_entrega(ordem, item)
    recursos = MagicMock()
    recursos.consumir_reserva = AsyncMock(
        side_effect=ResourceDependencyUnavailable("Etapa 2")
    )

    with pytest.raises(ResourceDependencyUnavailable):
        await EntregarOrdemServicoUseCase(
            uow=uow, recursos=recursos
        ).execute(str(ordem.id))

    repo.atualizar_item.assert_not_awaited()
    repo.atualizar.assert_not_awaited()
    uow.commit.assert_not_awaited()


async def test_entrega_reconcilia_pagamento_confirmado_e_preserva_versoes() -> None:
    ordem = replace(
        _ordem_liquidada(),
        payment_id=None,
        payment_status=None,
        payment_amount=None,
        payment_confirmed_at=None,
        payment_quote_version=None,
    )
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=ordem)
    repo.listar_itens_da_os = AsyncMock(return_value=[])
    repo.listar_servicos_da_os = AsyncMock(return_value=[])
    repo.atualizar = AsyncMock()
    uow = MagicMock()
    uow.ordem_servico_repo = repo
    uow.commit = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    financeiro = MagicMock()
    financeiro.obter_status = AsyncMock(
        return_value=FinancialStatus(
            order_id=ordem.id.value,
            quote=QuoteFinancialStatus(
                quote_id=uuid4(),
                quote_version=3,
                status="APROVADO",
                total=Decimal("270.00"),
            ),
            payment=PaymentFinancialStatus(
                payment_id=uuid4(),
                status="CONFIRMED",
                amount=Decimal("270.00"),
                confirmed_at=datetime.now(UTC),
            ),
            reconciled_at=datetime.now(UTC),
        )
    )

    response = await EntregarOrdemServicoUseCase(
        uow=uow,
        recursos=MagicMock(),
        financeiro=financeiro,
    ).execute(str(ordem.id))

    assert response.status == "ENTREGUE"
    assert [call.args[0].version for call in repo.atualizar.await_args_list] == [
        ordem.version + 1,
        ordem.version + 2,
    ]


async def test_entrega_falha_fechada_para_pagamento_unknown() -> None:
    ordem = replace(
        _ordem_liquidada(),
        payment_id=None,
        payment_status=None,
        payment_amount=None,
        payment_confirmed_at=None,
        payment_quote_version=None,
    )
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=ordem)
    repo.atualizar = AsyncMock()
    uow = MagicMock()
    uow.ordem_servico_repo = repo
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    financeiro = MagicMock()
    financeiro.obter_status = AsyncMock(
        return_value=FinancialStatus(
            order_id=ordem.id.value,
            quote=QuoteFinancialStatus(
                quote_id=uuid4(),
                quote_version=3,
                status="APROVADO",
                total=Decimal("270.00"),
            ),
            payment=PaymentFinancialStatus(
                payment_id=uuid4(),
                status="UNKNOWN",
                amount=Decimal("270.00"),
                confirmed_at=None,
            ),
            reconciled_at=datetime.now(UTC),
        )
    )

    with pytest.raises(OrdemServicoPagamentoNaoRegistradoError):
        await EntregarOrdemServicoUseCase(
            uow=uow,
            recursos=MagicMock(),
            financeiro=financeiro,
        ).execute(str(ordem.id))

    repo.atualizar.assert_not_awaited()
