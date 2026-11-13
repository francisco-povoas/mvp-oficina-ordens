from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.modules.clientes.domain.ports.cliente_repo import ClienteRepo
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.domain.ports.ordem_servico_repo import OrdemServicoRepo
from app.modules.ordens_servico.presentation.dependencies import (
    get_cliente_repo,
    get_ordem_servico_repo,
)
from app.shared.infra.api.dependencies.auth import RequireServiceScopes
from app.shared.value_objects.id import ID

router = APIRouter()

OrdersDep = Annotated[OrdemServicoRepo, Depends(get_ordem_servico_repo)]
CustomersDep = Annotated[ClienteRepo, Depends(get_cliente_repo)]
ApprovedQuoteId = Annotated[UUID, Query()]
ApprovedQuoteVersion = Annotated[int, Query(ge=1)]
ApprovedTotal = Annotated[Decimal, Query(gt=0)]


class SnapshotLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference_id: UUID
    description: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_price: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{2})$")


class CustomerContact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1)
    telefone: str = Field(min_length=1)


class QuoteContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    order_version: int = Field(ge=1)
    status: str
    customer_contact: CustomerContact
    services: list[SnapshotLine]
    items: list[SnapshotLine]


class PaymentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    order_version: int = Field(ge=1)
    status: str
    approved_quote_id: UUID
    approved_quote_version: int = Field(ge=1)
    approved_total: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{2})$")


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "ORDER_NOT_FOUND", "message": "Ordem não encontrada"},
    )


def _conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "ORDER_CONTEXT_CONFLICT", "message": message},
    )


@router.get(
    "/orders/{order_id}/quote-context",
    response_model=QuoteContext,
    dependencies=[Depends(RequireServiceScopes("orders:quote-context:read"))],
)
async def get_quote_context(
    order_id: UUID,
    orders: OrdersDep,
    customers: CustomersDep,
) -> QuoteContext:
    order = await orders.obter_por_id(ID(order_id))
    if order is None:
        raise _not_found()
    if order.status != StatusOrdemServico.DIAGNOSTICO_CONCLUIDO:
        raise _conflict("Ordem não está em DIAGNOSTICO_CONCLUIDO")
    customer = await customers.obter_por_id(ID(order.cliente_id))
    if customer is None:
        raise _conflict("Cliente da ordem não está disponível")
    email = customer.email.value if customer.email else ""
    telefone = customer.telefone.value if customer.telefone else ""
    if not email or not telefone:
        raise _conflict("Contato do cliente está incompleto")

    services = await orders.listar_servicos_da_os(ID(order_id))
    items = await orders.listar_itens_da_os(ID(order_id))
    return QuoteContext(
        order_id=order.id.value,
        order_version=order.version,
        status=order.status.value,
        customer_contact=CustomerContact(email=email, telefone=telefone),
        services=[
            SnapshotLine(
                reference_id=service.servico_id,
                description=service.descricao_servico or service.nome_servico,
                quantity=1,
                unit_price=_money(service.valor_unitario),
            )
            for service in services
            if not service.cancelado
        ],
        items=[
            SnapshotLine(
                reference_id=item.item_estoque_id,
                description=item.nome_item,
                quantity=item.quantidade,
                unit_price=_money(item.valor_unitario),
            )
            for item in items
            if item.status != StatusItemNaOS.CANCELADO
        ],
    )


@router.get(
    "/orders/{order_id}/payment-context",
    response_model=PaymentContext,
    dependencies=[Depends(RequireServiceScopes("orders:payment-context:read"))],
)
async def get_payment_context(
    order_id: UUID,
    approved_quote_id: ApprovedQuoteId,
    approved_quote_version: ApprovedQuoteVersion,
    approved_total: ApprovedTotal,
    orders: OrdersDep,
) -> PaymentContext:
    order = await orders.obter_por_id(ID(order_id))
    if order is None:
        raise _not_found()
    if order.status != StatusOrdemServico.FINALIZADA:
        raise _conflict("Ordem não está FINALIZADA")

    services = await orders.listar_servicos_da_os(ID(order_id))
    items = await orders.listar_itens_da_os(ID(order_id))
    snapshot_total = sum(
        (service.valor_unitario for service in services if not service.cancelado),
        Decimal("0"),
    ) + sum(
        (
            item.valor_unitario * item.quantidade
            for item in items
            if item.status != StatusItemNaOS.CANCELADO
        ),
        Decimal("0"),
    )
    if snapshot_total.quantize(Decimal("0.01")) != approved_total.quantize(
        Decimal("0.01")
    ):
        raise _conflict("Total aprovado diverge dos snapshots da ordem")
    return PaymentContext(
        order_id=order.id.value,
        order_version=order.version,
        status=order.status.value,
        approved_quote_id=approved_quote_id,
        approved_quote_version=approved_quote_version,
        approved_total=_money(snapshot_total),
    )

