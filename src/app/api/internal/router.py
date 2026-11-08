from fastapi import APIRouter, Depends

from app.api.internal import auth, orders
from app.http_contract import require_correlation_id

router = APIRouter(
    prefix="/internal/v1",
    include_in_schema=True,
    dependencies=[Depends(require_correlation_id)],
)
router.include_router(auth.router, tags=["Autenticação interna"])
router.include_router(orders.router, tags=["Contratos internos de Ordens"])

