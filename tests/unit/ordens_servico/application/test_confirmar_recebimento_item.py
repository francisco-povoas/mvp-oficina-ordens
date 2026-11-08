"""Recebimento atualiza somente o agregado de Ordens após resultado de Recursos."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.integrations.resources import (
    ReceberItemPendenteResult,
    ResourceDependencyUnavailable,
)
from app.modules.ordens_servico.application.use_cases.confirmar_recebimento_item import (
    ConfirmarRecebimentoItemDaOrdemServicoUseCase,
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
    ItemOrdemServicoStatusInvalidoError,
    OrdemServicoItemNaoEncontradoError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoTransicaoInvalidaError,
)
from app.shared.value_objects.id import ID


def _ordem(status: StatusOrdemServico = StatusOrdemServico.AGUARDANDO_ITENS) -> OrdemServico:
    agora = datetime.now(UTC)
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=status,
        queixa_inicial="Ruído",
        diagnostico="Peça desgastada",
        criado_em=agora,
        atualizado_em=agora,
        iniciado_diagnostico_em=agora,
        diagnostico_concluido_em=agora,
        version=7,
    )


def _item(ordem: OrdemServico, status: StatusItemNaOS = StatusItemNaOS.A_RECEBER):
    return OrdemServicoItem(
        id=ID.generate(),
        ordem_servico_id=ordem.id.value,
        item_estoque_id=uuid4(),
        nome_item="Correia",
        tipo_item="PECA",
        quantidade=2,
        valor_unitario=Decimal("80.00"),
        status=status,
    )


def _dependencias(ordem: OrdemServico | None, item: OrdemServicoItem | None):
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=ordem)
    repo.obter_item_por_id = AsyncMock(return_value=item)
    repo.listar_itens_da_os = AsyncMock(return_value=[] if item is None else [item])
    repo.listar_servicos_da_os = AsyncMock(return_value=[])
    repo.atualizar_item = AsyncMock()
    repo.atualizar = AsyncMock()

    uow = MagicMock()
    uow.ordem_servico_repo = repo
    uow.commit = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)

    recursos = MagicMock()
    recursos.receber_item_pendente = AsyncMock()
    return repo, uow, recursos


def _confirmar(command):
    return ReceberItemPendenteResult(
        operation_id=command.operation_id,
        recurso_id=command.recurso_id,
        quantidade=command.quantidade,
        reservation_id=uuid4(),
        recebido_em=datetime.now(UTC),
    )


async def test_confirma_resultado_e_avanca_versao_sem_manipular_saldo() -> None:
    ordem = _ordem()
    item = _item(ordem)
    repo, uow, recursos = _dependencias(ordem, item)
    recursos.receber_item_pendente.side_effect = _confirmar

    resposta = await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
        uow=uow, recursos=recursos
    ).execute(str(ordem.id), str(item.id))

    command = recursos.receber_item_pendente.await_args.args[0]
    assert command.recurso_id == item.item_estoque_id
    assert command.quantidade == item.quantidade
    assert resposta.itens[0].status == StatusItemNaOS.RESERVADO.value
    assert resposta.status == StatusOrdemServico.APROVADA.value
    ordem_atualizada = repo.atualizar.await_args.args[0]
    assert ordem_atualizada.version == 8
    repo.atualizar_item.assert_awaited_once()
    uow.commit.assert_awaited_once()


async def test_mantem_aguardando_quando_outro_item_continua_pendente() -> None:
    ordem = _ordem()
    item = _item(ordem)
    outro = _item(ordem)
    repo, uow, recursos = _dependencias(ordem, item)
    repo.listar_itens_da_os.return_value = [item, outro]
    recursos.receber_item_pendente.side_effect = _confirmar

    resposta = await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
        uow=uow, recursos=recursos
    ).execute(str(ordem.id), str(item.id))

    assert resposta.status == StatusOrdemServico.AGUARDANDO_ITENS.value
    assert repo.atualizar.await_args.args[0].version == 8


async def test_indisponibilidade_nao_consolida_estado_local() -> None:
    ordem = _ordem()
    item = _item(ordem)
    repo, uow, recursos = _dependencias(ordem, item)
    recursos.receber_item_pendente.side_effect = ResourceDependencyUnavailable("Etapa 2")

    with pytest.raises(ResourceDependencyUnavailable):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
            uow=uow, recursos=recursos
        ).execute(str(ordem.id), str(item.id))

    repo.atualizar_item.assert_not_awaited()
    repo.atualizar.assert_not_awaited()
    uow.commit.assert_not_awaited()


async def test_rejeita_os_inexistente_antes_de_chamar_recursos() -> None:
    repo, uow, recursos = _dependencias(None, None)
    with pytest.raises(OrdemServicoNaoEncontradaError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
            uow=uow, recursos=recursos
        ).execute(str(uuid4()), str(uuid4()))
    recursos.receber_item_pendente.assert_not_awaited()
    repo.atualizar.assert_not_awaited()


async def test_rejeita_status_da_os_invalido() -> None:
    ordem = _ordem(StatusOrdemServico.APROVADA)
    item = _item(ordem)
    _, uow, recursos = _dependencias(ordem, item)
    with pytest.raises(OrdemServicoTransicaoInvalidaError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
            uow=uow, recursos=recursos
        ).execute(str(ordem.id), str(item.id))


async def test_rejeita_item_de_outra_os() -> None:
    ordem = _ordem()
    item = _item(_ordem())
    _, uow, recursos = _dependencias(ordem, item)
    with pytest.raises(OrdemServicoItemNaoEncontradoError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
            uow=uow, recursos=recursos
        ).execute(str(ordem.id), str(item.id))


async def test_rejeita_item_que_nao_esta_a_receber() -> None:
    ordem = _ordem()
    item = _item(ordem, StatusItemNaOS.RESERVADO)
    _, uow, recursos = _dependencias(ordem, item)
    with pytest.raises(ItemOrdemServicoStatusInvalidoError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(
            uow=uow, recursos=recursos
        ).execute(str(ordem.id), str(item.id))
