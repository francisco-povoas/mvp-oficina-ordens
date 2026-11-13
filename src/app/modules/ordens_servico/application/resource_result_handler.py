"""Despacho fail-closed dos resultados de Recursos suportados por Ordens."""

from collections.abc import Mapping

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.application.distributed_stock_operation_result_handler import (
    DistributedStockOperationResultHandler,
)
from app.modules.ordens_servico.application.reservation_saga_result_handler import (
    ReservationSagaResultHandler,
)


class ResourceResultHandler:
    def __init__(self) -> None:
        self.saga_handler = ReservationSagaResultHandler()
        self.operation_handler = DistributedStockOperationResultHandler()

    async def handle(
        self, envelope: Mapping[str, object], session: AsyncSession
    ) -> None:
        message_type = str(envelope["message_type"])
        if message_type in {
            "ResourceReserved",
            "ItemBackordered",
            "ReservationRejected",
        } or (message_type == "ReservationReleased" and envelope.get("saga_id")):
            await self.saga_handler.handle(envelope, session)
            return
        await self.operation_handler.handle(envelope, session)
