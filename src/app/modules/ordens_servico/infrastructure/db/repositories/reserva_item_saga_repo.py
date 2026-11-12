"""Persistência otimista da Saga de reserva de item."""

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import select

from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    CodigoRejeicaoReservaItemSaga,
    ComandoReservaItemSaga,
    EstadoReservaItemSaga,
    ReservaItemSaga,
    ResultadoTerminalReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ReservaItemSagaConflitoDeVersaoError,
)
from app.modules.ordens_servico.infrastructure.db.models.reserva_item_saga_model import (
    ReservaItemSagaModel,
)
from app.shared.infra.db import DBSession


class ReservaItemSagaRepo:
    def __init__(self, session: DBSession) -> None:
        self.session = session

    async def salvar(self, saga: ReservaItemSaga) -> None:
        self.session.add(self._to_model(saga))

    async def obter_por_id(self, saga_id: UUID) -> ReservaItemSaga | None:
        model = await self.session.get(ReservaItemSagaModel, saga_id)
        return self._to_entity(model) if model else None

    async def obter_por_operation_id(self, operation_id: UUID) -> ReservaItemSaga | None:
        result = await self.session.exec(
            select(ReservaItemSagaModel).where(
                ReservaItemSagaModel.operation_id == operation_id
            )
        )
        model = result.first()
        return self._to_entity(model) if model else None

    async def obter_por_operation_id_com_lock(
        self, operation_id: UUID
    ) -> ReservaItemSaga | None:
        result = await self.session.exec(
            select(ReservaItemSagaModel)
            .where(ReservaItemSagaModel.operation_id == operation_id)
            .with_for_update()
        )
        model = result.first()
        return self._to_entity(model) if model else None

    async def obter_por_chave_idempotencia(
        self,
        *,
        ordem_servico_id: UUID,
        idempotency_key: str,
    ) -> ReservaItemSaga | None:
        result = await self.session.exec(
            select(ReservaItemSagaModel).where(
                ReservaItemSagaModel.ordem_servico_id == ordem_servico_id,
                ReservaItemSagaModel.idempotency_key == idempotency_key,
            )
        )
        model = result.first()
        return self._to_entity(model) if model else None

    async def listar_nao_terminais_da_os(
        self,
        ordem_servico_id: UUID,
    ) -> list[ReservaItemSaga]:
        result = await self.session.exec(
            select(ReservaItemSagaModel)
            .where(
                ReservaItemSagaModel.ordem_servico_id == ordem_servico_id,
                ReservaItemSagaModel.estado.notin_(
                    (
                        EstadoReservaItemSaga.COMPLETED.value,
                        EstadoReservaItemSaga.COMPENSATED.value,
                        EstadoReservaItemSaga.REJECTED.value,
                    )
                ),
            )
            .order_by(ReservaItemSagaModel.criado_em, ReservaItemSagaModel.id)
        )
        return [self._to_entity(model) for model in result.all()]

    async def atualizar(self, saga: ReservaItemSaga) -> ReservaItemSaga:
        versao_anterior = saga.version - 1
        if versao_anterior < 1:
            raise ReservaItemSagaConflitoDeVersaoError(
                "A atualização da Saga deve avançar exatamente uma versão."
            )
        values = self._values(saga)
        result = await self.session.exec(
            sa.update(ReservaItemSagaModel)
            .where(
                ReservaItemSagaModel.id == saga.id,
                ReservaItemSagaModel.version == versao_anterior,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            existente = await self.session.get(ReservaItemSagaModel, saga.id)
            if existente is None:
                raise ReservaItemSagaConflitoDeVersaoError("Saga não encontrada para atualização.")
            raise ReservaItemSagaConflitoDeVersaoError(
                f"Conflito de versão na Saga '{saga.id}': "
                f"esperada {versao_anterior}, atual {existente.version}."
            )
        return saga

    @staticmethod
    def _to_model(saga: ReservaItemSaga) -> ReservaItemSagaModel:
        return ReservaItemSagaModel(id=saga.id, **ReservaItemSagaRepo._values(saga))

    @staticmethod
    def _values(saga: ReservaItemSaga) -> dict[str, object]:
        return {
            "operation_id": saga.operation_id,
            "ordem_servico_id": saga.ordem_servico_id,
            "ordem_servico_item_id": saga.ordem_servico_item_id,
            "resource_id": saga.resource_id,
            "quantity": saga.quantity,
            "correlation_id": saga.correlation_id,
            "idempotency_key": saga.idempotency_key,
            "request_hash": saga.request_hash,
            "estado": saga.estado.value,
            "comando_pendente": (
                saga.comando_pendente.value if saga.comando_pendente else None
            ),
            "ultimo_comando_message_id": saga.ultimo_comando_message_id,
            "terminal_result": saga.terminal_result.value if saga.terminal_result else None,
            "reservation_id": saga.reservation_id,
            "rejection_code": saga.rejection_code.value if saga.rejection_code else None,
            "rejection_detail": saga.rejection_detail,
            "ultimo_resultado_type": saga.ultimo_resultado_type,
            "ultimo_resultado_message_id": saga.ultimo_resultado_message_id,
            "ultimo_resultado_em": saga.ultimo_resultado_em,
            "cancellation_requested_at": saga.cancellation_requested_at,
            "terminal_at": saga.terminal_at,
            "resource_name": saga.resource_name,
            "resource_description": saga.resource_description,
            "resource_unit_price": saga.resource_unit_price,
            "criado_em": saga.criado_em,
            "atualizado_em": saga.atualizado_em,
            "version": saga.version,
        }

    @staticmethod
    def _to_entity(model: ReservaItemSagaModel) -> ReservaItemSaga:
        return ReservaItemSaga(
            id=model.id,
            operation_id=model.operation_id,
            ordem_servico_id=model.ordem_servico_id,
            ordem_servico_item_id=model.ordem_servico_item_id,
            resource_id=model.resource_id,
            quantity=model.quantity,
            correlation_id=model.correlation_id,
            idempotency_key=model.idempotency_key,
            request_hash=model.request_hash,
            estado=EstadoReservaItemSaga(model.estado),
            comando_pendente=(
                ComandoReservaItemSaga(model.comando_pendente)
                if model.comando_pendente
                else None
            ),
            ultimo_comando_message_id=model.ultimo_comando_message_id,
            terminal_result=(
                ResultadoTerminalReservaItemSaga(model.terminal_result)
                if model.terminal_result
                else None
            ),
            reservation_id=model.reservation_id,
            rejection_code=(
                CodigoRejeicaoReservaItemSaga(model.rejection_code)
                if model.rejection_code
                else None
            ),
            rejection_detail=model.rejection_detail,
            ultimo_resultado_type=model.ultimo_resultado_type,
            ultimo_resultado_message_id=model.ultimo_resultado_message_id,
            ultimo_resultado_em=model.ultimo_resultado_em,
            cancellation_requested_at=model.cancellation_requested_at,
            terminal_at=model.terminal_at,
            resource_name=model.resource_name,
            resource_description=model.resource_description,
            resource_unit_price=(
                Decimal(str(model.resource_unit_price))
                if model.resource_unit_price is not None
                else None
            ),
            criado_em=model.criado_em,
            atualizado_em=model.atualizado_em,
            version=model.version,
        )
