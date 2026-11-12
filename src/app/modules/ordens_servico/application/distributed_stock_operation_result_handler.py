"""Aplica resultados autoritativos das coordenações distribuídas leves."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationResult,
    DistributedOperationType,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.domain.exceptions import DistributedOperationInvalidError
from app.modules.ordens_servico.infrastructure.db.repositories.distributed_operation_repo import (
    PostgresDistributedOperationRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.shared.value_objects.id import ID


class DistributedStockOperationResultHandler:
    """Usa a sessão transacional já aberta pela Inbox de Ordens."""

    _SUPPORTED_TYPES = {"ReservationReleased"}

    async def handle(
        self, envelope: Mapping[str, object], session: AsyncSession
    ) -> None:
        message_type = str(envelope["message_type"])
        if message_type not in self._SUPPORTED_TYPES:
            raise DistributedOperationInvalidError(
                f"Resultado distribuído ainda não suportado: '{message_type}'."
            )
        operation_id = self._uuid(envelope, "operation_id")
        repo = PostgresDistributedOperationRepo(session)
        operation = await repo.get_by_operation_id_for_update(operation_id)
        if operation is None:
            raise DistributedOperationInvalidError(
                f"Operação distribuída '{operation_id}' não encontrada."
            )
        self._validate_envelope(envelope, operation)
        payload = self._payload(envelope)
        self._validate_release(payload, operation)
        result_at = self._timestamp(payload, "released_at")
        completed = operation.complete(
            result=DistributedOperationResult.RESERVATION_RELEASED,
            result_message_id=self._uuid(envelope, "message_id"),
            reservation_id=self._uuid(payload, "reservation_id"),
            received_at=result_at,
        )

        order_repo = OrdemServicoRepo(session)
        order = await order_repo.obter_por_id(ID(operation.ordem_servico_id))
        item = await order_repo.obter_item_por_id(ID(operation.ordem_servico_item_id))
        if order is None or item is None or item.ordem_servico_id != order.id.value:
            raise DistributedOperationInvalidError(
                "A liberação não corresponde a um item persistido da OS."
            )
        if item.status is not StatusItemNaOS.RESERVADO:
            raise DistributedOperationInvalidError(
                "Somente item RESERVADO pode ser cancelado após liberação."
            )
        await order_repo.atualizar_item(replace(item, status=StatusItemNaOS.CANCELADO))
        await order_repo.atualizar(order.registrar_alteracao())
        await repo.update(completed)
        await self._after_effects(message_type, session)

    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        """Ponto de falha injetável para provar rollback da Inbox."""

    @classmethod
    def _validate_envelope(
        cls,
        envelope: Mapping[str, object],
        operation: DistributedOperation,
    ) -> None:
        if int(envelope["message_version"]) != 1:
            raise DistributedOperationInvalidError("Somente resultados v1 são aceitos.")
        if envelope.get("saga_id") is not None:
            raise DistributedOperationInvalidError(
                "Operação distribuída fora da Saga exige saga_id nulo."
            )
        if cls._uuid(envelope, "correlation_id") != operation.correlation_id:
            raise DistributedOperationInvalidError("correlation_id divergente.")
        if cls._uuid(envelope, "causation_id") != operation.command_message_id:
            raise DistributedOperationInvalidError("causation_id divergente.")
        if operation.operation_type is not DistributedOperationType.RELEASE:
            raise DistributedOperationInvalidError(
                "ReservationReleased pertence a uma operação de outro tipo."
            )

    @classmethod
    def _validate_release(
        cls,
        payload: Mapping[str, object],
        operation: DistributedOperation,
    ) -> None:
        if cls._uuid(payload, "reservation_id") != operation.reservation_id:
            raise DistributedOperationInvalidError("reservation_id divergente.")
        if cls._uuid(payload, "resource_id") != operation.resource_id:
            raise DistributedOperationInvalidError("resource_id divergente.")
        quantity = payload.get("quantity")
        if isinstance(quantity, bool) or quantity != operation.quantity:
            raise DistributedOperationInvalidError("quantity divergente.")

    @staticmethod
    def _payload(envelope: Mapping[str, object]) -> Mapping[str, object]:
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise DistributedOperationInvalidError("Payload do resultado inválido.")
        return payload

    @staticmethod
    def _uuid(source: Mapping[str, object], field: str) -> UUID:
        try:
            return UUID(str(source[field]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DistributedOperationInvalidError(
                f"Campo UUID '{field}' inválido."
            ) from exc

    @staticmethod
    def _timestamp(source: Mapping[str, object], field: str) -> datetime:
        try:
            value = datetime.fromisoformat(str(source[field]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise DistributedOperationInvalidError(
                f"Timestamp '{field}' inválido."
            ) from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise DistributedOperationInvalidError(
                f"Timestamp '{field}' deve possuir timezone."
            )
        return value.astimezone(UTC)
