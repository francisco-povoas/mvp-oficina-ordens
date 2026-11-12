"""Entrega fail-closed coordenada por operações de consumo."""

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
from app.modules.ordens_servico.application.use_cases.entregar_ordem_servico import (
    EntregarOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperationType,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import ReservaItemSaga
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoPagamentoNaoRegistradoError,
)
from app.shared.value_objects.id import ID


def _paid_order() -> OrdemServico:
    now = datetime.now(UTC)
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=StatusOrdemServico.FINALIZADA,
        queixa_inicial="Ruído no motor",
        diagnostico="Correia desgastada",
        criado_em=now,
        atualizado_em=now,
        iniciado_diagnostico_em=now,
        diagnostico_concluido_em=now,
        payment_id=uuid4(),
        payment_status="CONFIRMED",
        payment_amount=Decimal("270.00"),
        payment_confirmed_at=now,
        payment_quote_version=1,
    )


def _item_in_use(order: OrdemServico) -> OrdemServicoItem:
    return OrdemServicoItem(
        id=ID.generate(),
        ordem_servico_id=order.id.value,
        item_estoque_id=uuid4(),
        nome_item="Correia",
        tipo_item="PECA",
        quantidade=1,
        valor_unitario=Decimal("80.00"),
        status=StatusItemNaOS.EM_USO,
    )


def _reserved_saga(order: OrdemServico, item: OrdemServicoItem) -> ReservaItemSaga:
    saga = ReservaItemSaga.iniciar(
        saga_id=uuid4(),
        operation_id=uuid4(),
        ordem_servico_id=order.id.value,
        ordem_servico_item_id=item.id.value,
        resource_id=item.item_estoque_id,
        quantity=item.quantidade,
        correlation_id=uuid4(),
        idempotency_key=f"delivery-{uuid4()}",
        request_hash="a" * 64,
        reserve_message_id=uuid4(),
    )
    return saga.registrar_reserva(
        reservation_id=uuid4(),
        message_id=uuid4(),
        resultado_em=datetime.now(UTC),
        resource_name=item.nome_item,
        resource_description=None,
        resource_unit_price=item.valor_unitario,
    )


def _uow(order: OrdemServico, items: list[OrdemServicoItem]):
    order_repo = MagicMock()
    order_repo.obter_por_id_com_lock = AsyncMock(return_value=order)
    order_repo.listar_itens_da_os = AsyncMock(return_value=items)
    order_repo.atualizar = AsyncMock()
    operation_repo = MagicMock()
    operation_repo.get_latest_completed_for_item = AsyncMock(return_value=None)
    operation_repo.get_by_business_key = AsyncMock(return_value=None)
    operation_repo.save = AsyncMock()
    saga_repo = MagicMock()
    saga_repo.obter_por_ordem_servico_item_id = AsyncMock(return_value=None)
    uow = MagicMock()
    uow.ordem_servico_repo = order_repo
    uow.distributed_operation_repo = operation_repo
    uow.reserva_item_saga_repo = saga_repo
    uow.outbox = MagicMock()
    uow.outbox.add = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    return order_repo, operation_repo, saga_repo, uow


async def test_entrega_sem_itens_conclui_imediatamente() -> None:
    order = _paid_order()
    order_repo, _, _, uow = _uow(order, [])

    response = await EntregarOrdemServicoUseCase(uow=uow).execute(
        str(order.id), correlation_id=uuid4()
    )

    assert response.state == "COMPLETED"
    assert response.order_status == "ENTREGUE"
    assert response.operation_ids == []
    assert order_repo.atualizar.await_args.args[0].status is StatusOrdemServico.ENTREGUE


async def test_entrega_com_item_em_uso_emite_consumo_sem_entregar() -> None:
    order = _paid_order()
    item = _item_in_use(order)
    order_repo, operation_repo, saga_repo, uow = _uow(order, [item])
    saga_repo.obter_por_ordem_servico_item_id.return_value = _reserved_saga(order, item)

    response = await EntregarOrdemServicoUseCase(uow=uow).execute(
        str(order.id), correlation_id=uuid4()
    )

    assert response.state == "PENDING"
    assert response.order_status == "FINALIZADA"
    operation_repo.save.assert_awaited_once()
    operation = operation_repo.save.await_args.args[0]
    assert operation.operation_type is DistributedOperationType.CONSUME
    assert operation.reservation_id is not None
    assert uow.outbox.add.await_args.args[0].event_type == "ConsumeReservation.v1"
    order_repo.atualizar.assert_not_awaited()


async def test_reconcilia_pagamento_e_entrega_sem_consumos() -> None:
    order = replace(
        _paid_order(),
        payment_id=None,
        payment_status=None,
        payment_amount=None,
        payment_confirmed_at=None,
        payment_quote_version=None,
    )
    order_repo, _, _, uow = _uow(order, [])
    financeiro = MagicMock()
    financeiro.obter_status = AsyncMock(
        return_value=FinancialStatus(
            order_id=order.id.value,
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
        financeiro=financeiro,
    ).execute(str(order.id), correlation_id=uuid4())

    assert response.order_status == "ENTREGUE"
    assert [call.args[0].version for call in order_repo.atualizar.await_args_list] == [
        order.version + 1,
        order.version + 2,
    ]


async def test_falha_fechada_para_pagamento_unknown() -> None:
    order = replace(
        _paid_order(),
        payment_id=None,
        payment_status=None,
        payment_amount=None,
        payment_confirmed_at=None,
        payment_quote_version=None,
    )
    order_repo, _, _, uow = _uow(order, [])
    financeiro = MagicMock()
    financeiro.obter_status = AsyncMock(
        return_value=FinancialStatus(
            order_id=order.id.value,
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
            financeiro=financeiro,
        ).execute(str(order.id), correlation_id=uuid4())

    order_repo.atualizar.assert_not_awaited()
