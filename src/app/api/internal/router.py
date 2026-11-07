from fastapi import APIRouter

from app.api.internal import auth, orders

router = APIRouter(prefix="/internal/v1", include_in_schema=True)
router.include_router(auth.router, tags=["Autenticação interna"])
router.include_router(orders.router, tags=["Contratos internos de Ordens"])

