"""Porta de persistência da Saga de reserva de item."""

from typing import Protocol
from uuid import UUID

from app.modules.ordens_servico.domain.entities.reserva_item_saga import ReservaItemSaga


class ReservaItemSagaRepo(Protocol):
    async def salvar(self, saga: ReservaItemSaga) -> None: ...

    async def obter_por_id(self, saga_id: UUID) -> ReservaItemSaga | None: ...

    async def obter_por_operation_id(self, operation_id: UUID) -> ReservaItemSaga | None: ...

    async def obter_por_chave_idempotencia(
        self,
        *,
        ordem_servico_id: UUID,
        idempotency_key: str,
    ) -> ReservaItemSaga | None: ...

    async def listar_nao_terminais_da_os(
        self,
        ordem_servico_id: UUID,
    ) -> list[ReservaItemSaga]: ...

    async def atualizar(self, saga: ReservaItemSaga) -> ReservaItemSaga: ...
