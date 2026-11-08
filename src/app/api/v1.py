from fastapi import APIRouter

from app.modules.clientes.presentation.routers import cliente
from app.modules.iam.presentation.routers import auth, usuario
from app.modules.ordens_servico.presentation.routers import ordem_servico
from app.modules.veiculos.presentation.routers import veiculo

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router, prefix="/auth", tags=["Auth"])
router.include_router(usuario.router, prefix="/usuarios", tags=["Usuários"])
router.include_router(cliente.router, prefix="/clientes", tags=["Clientes"])
router.include_router(veiculo.router, prefix="/veiculos", tags=["Veículos"])
router.include_router(ordem_servico.router, prefix="/ordens-servico", tags=["Ordens de Serviço"])
