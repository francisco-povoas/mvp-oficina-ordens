"""Início assíncrono do recebimento de item pendente."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

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


def _order(status: StatusOrdemServico = StatusOrdemServico.AGUARDANDO_ITENS):
    now = datetime.now(UTC)
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=status,
        queixa_inicial="Ruído",
        diagnostico="Peça desgastada",
        criado_em=now,
        atualizado_em=now,
        iniciado_diagnostico_em=now,
        diagnostico_concluido_em=now,
        version=7,
    )


def _item(order: OrdemServico, status: StatusItemNaOS = StatusItemNaOS.A_RECEBER):
    return OrdemServicoItem(
        id=ID.generate(),
        ordem_servico_id=order.id.value,
        item_estoque_id=uuid4(),
        nome_item="Correia",
        tipo_item="PECA",
        quantidade=2,
        valor_unitario=Decimal("80.00"),
        status=status,
    )


def _uow(order: OrdemServico | None, item: OrdemServicoItem | None):
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=order)
    repo.obter_item_por_id_com_lock = AsyncMock(return_value=item)
    repo.atualizar_item = AsyncMock()
    repo.atualizar = AsyncMock()
    operation_repo = MagicMock()
    operation_repo.get_by_business_key = AsyncMock(return_value=None)
    operation_repo.save = AsyncMock()
    uow = MagicMock()
    uow.ordem_servico_repo = repo
    uow.distributed_operation_repo = operation_repo
    uow.outbox = MagicMock()
    uow.outbox.add = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    return repo, operation_repo, uow


async def test_persiste_operacao_e_outbox_sem_confirmar_item_local() -> None:
    order = _order()
    item = _item(order)
    repo, operation_repo, uow = _uow(order, item)

    response = await ConfirmarRecebimentoItemDaOrdemServicoUseCase(uow=uow).execute(
        str(order.id), str(item.id), correlation_id=uuid4()
    )

    assert response.operation_type == "RECEIVE"
    assert response.state == "PENDING"
    operation_repo.save.assert_awaited_once()
    persisted = operation_repo.save.await_args.args[0]
    assert persisted.reservation_id is None
    outbox = uow.outbox.add.await_args.args[0]
    assert outbox.event_type == "ReceiveBackorderedItem.v1"
    assert outbox.saga_id is None
    repo.atualizar_item.assert_not_awaited()
    repo.atualizar.assert_not_awaited()


async def test_rejeita_os_inexistente() -> None:
    _, _, uow = _uow(None, None)
    with pytest.raises(OrdemServicoNaoEncontradaError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(uow=uow).execute(
            str(uuid4()), str(uuid4()), correlation_id=uuid4()
        )


async def test_rejeita_status_da_os_invalido() -> None:
    order = _order(StatusOrdemServico.APROVADA)
    item = _item(order)
    _, _, uow = _uow(order, item)
    with pytest.raises(OrdemServicoTransicaoInvalidaError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(uow=uow).execute(
            str(order.id), str(item.id), correlation_id=uuid4()
        )


async def test_rejeita_item_de_outra_os() -> None:
    order = _order()
    item = _item(_order())
    _, _, uow = _uow(order, item)
    with pytest.raises(OrdemServicoItemNaoEncontradoError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(uow=uow).execute(
            str(order.id), str(item.id), correlation_id=uuid4()
        )


async def test_rejeita_item_que_nao_esta_a_receber() -> None:
    order = _order()
    item = _item(order, StatusItemNaOS.RESERVADO)
    _, _, uow = _uow(order, item)
    with pytest.raises(ItemOrdemServicoStatusInvalidoError):
        await ConfirmarRecebimentoItemDaOrdemServicoUseCase(uow=uow).execute(
            str(order.id), str(item.id), correlation_id=uuid4()
        )
