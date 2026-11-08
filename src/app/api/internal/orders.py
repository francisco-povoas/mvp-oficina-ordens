from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.shared.infra.api.dependencies.auth import RequireServiceScopes

router = APIRouter()


@router.get(
    "/orders/{order_id}/quote-context",
    dependencies=[Depends(RequireServiceScopes("orders:quote-context:read"))],
)
async def get_quote_context(order_id: UUID) -> None:
    raise HTTPException(
        status_code=501,
        detail={
            "code": "INTEGRATION_PLANNED",
            "message": "Consulta funcional planejada para a Etapa 2",
            "order_id": str(order_id),
        },
    )


@router.get(
    "/orders/{order_id}/payment-context",
    dependencies=[Depends(RequireServiceScopes("orders:payment-context:read"))],
)
async def get_payment_context(order_id: UUID) -> None:
    raise HTTPException(
        status_code=501,
        detail={
            "code": "INTEGRATION_PLANNED",
            "message": "Consulta funcional planejada para a Etapa 2",
            "order_id": str(order_id),
        },
    )

