"""Persistência otimista das coordenações distribuídas leves."""

from uuid import UUID

import sqlalchemy as sa
from sqlmodel import select

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationResult,
    DistributedOperationState,
    DistributedOperationType,
)
from app.modules.ordens_servico.domain.exceptions import (
    DistributedOperationVersionConflictError,
)
from app.modules.ordens_servico.infrastructure.db.models.distributed_operation_model import (
    DistributedOperationModel,
)
from app.shared.infra.db import DBSession


class PostgresDistributedOperationRepo:
    def __init__(self, session: DBSession) -> None:
        self.session = session

    async def save(self, operation: DistributedOperation) -> None:
        self.session.add(self._to_model(operation))

    async def get_by_operation_id(
        self, operation_id: UUID
    ) -> DistributedOperation | None:
        model = await self.session.get(DistributedOperationModel, operation_id)
        return self._to_entity(model) if model else None

    async def get_by_operation_id_for_update(
        self, operation_id: UUID
    ) -> DistributedOperation | None:
        result = await self.session.exec(
            select(DistributedOperationModel)
            .where(DistributedOperationModel.operation_id == operation_id)
            .with_for_update()
        )
        model = result.first()
        return self._to_entity(model) if model else None

    async def get_by_business_key(
        self, business_key: str
    ) -> DistributedOperation | None:
        result = await self.session.exec(
            select(DistributedOperationModel).where(
                DistributedOperationModel.business_key == business_key
            )
        )
        model = result.first()
        return self._to_entity(model) if model else None

    async def list_pending_for_order(
        self,
        ordem_servico_id: UUID,
        operation_type: DistributedOperationType | None = None,
    ) -> list[DistributedOperation]:
        filters = [
            DistributedOperationModel.ordem_servico_id == ordem_servico_id,
            DistributedOperationModel.state == DistributedOperationState.PENDING.value,
        ]
        if operation_type is not None:
            filters.append(
                DistributedOperationModel.operation_type == operation_type.value
            )
        result = await self.session.exec(
            select(DistributedOperationModel)
            .where(*filters)
            .order_by(
                DistributedOperationModel.created_at,
                DistributedOperationModel.operation_id,
            )
        )
        return [self._to_entity(model) for model in result.all()]

    async def get_latest_completed_for_item(
        self,
        ordem_servico_item_id: UUID,
        operation_type: DistributedOperationType,
    ) -> DistributedOperation | None:
        result = await self.session.exec(
            select(DistributedOperationModel)
            .where(
                DistributedOperationModel.ordem_servico_item_id
                == ordem_servico_item_id,
                DistributedOperationModel.operation_type == operation_type.value,
                DistributedOperationModel.state
                == DistributedOperationState.COMPLETED.value,
            )
            .order_by(DistributedOperationModel.terminal_at.desc())
            .limit(1)
        )
        model = result.first()
        return self._to_entity(model) if model else None

    async def update(self, operation: DistributedOperation) -> DistributedOperation:
        previous_version = operation.version - 1
        if previous_version < 1:
            raise DistributedOperationVersionConflictError(
                "A atualização deve avançar exatamente uma versão."
            )
        result = await self.session.exec(
            sa.update(DistributedOperationModel)
            .where(
                DistributedOperationModel.operation_id == operation.operation_id,
                DistributedOperationModel.version == previous_version,
            )
            .values(**self._values(operation))
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise DistributedOperationVersionConflictError(
                f"Conflito de versão na operação '{operation.operation_id}'."
            )
        return operation

    @classmethod
    def _to_model(cls, operation: DistributedOperation) -> DistributedOperationModel:
        return DistributedOperationModel(
            operation_id=operation.operation_id,
            **cls._values(operation),
        )

    @staticmethod
    def _values(operation: DistributedOperation) -> dict[str, object]:
        return {
            "operation_type": operation.operation_type.value,
            "ordem_servico_id": operation.ordem_servico_id,
            "ordem_servico_item_id": operation.ordem_servico_item_id,
            "resource_id": operation.resource_id,
            "quantity": operation.quantity,
            "business_key": operation.business_key,
            "request_hash": operation.request_hash,
            "correlation_id": operation.correlation_id,
            "state": operation.state.value,
            "command_type": operation.command_type,
            "command_message_id": operation.command_message_id,
            "command_created_at": operation.command_created_at,
            "reservation_id": operation.reservation_id,
            "result_type": operation.result_type,
            "result_message_id": operation.result_message_id,
            "result_received_at": operation.result_received_at,
            "terminal_result": (
                operation.terminal_result.value if operation.terminal_result else None
            ),
            "terminal_at": operation.terminal_at,
            "created_at": operation.created_at,
            "updated_at": operation.updated_at,
            "version": operation.version,
        }

    @staticmethod
    def _to_entity(model: DistributedOperationModel) -> DistributedOperation:
        return DistributedOperation(
            operation_id=model.operation_id,
            operation_type=DistributedOperationType(model.operation_type),
            ordem_servico_id=model.ordem_servico_id,
            ordem_servico_item_id=model.ordem_servico_item_id,
            resource_id=model.resource_id,
            quantity=model.quantity,
            business_key=model.business_key,
            request_hash=model.request_hash,
            correlation_id=model.correlation_id,
            state=DistributedOperationState(model.state),
            command_type=model.command_type,
            command_message_id=model.command_message_id,
            command_created_at=model.command_created_at,
            reservation_id=model.reservation_id,
            result_type=model.result_type,
            result_message_id=model.result_message_id,
            result_received_at=model.result_received_at,
            terminal_result=(
                DistributedOperationResult(model.terminal_result)
                if model.terminal_result
                else None
            ),
            terminal_at=model.terminal_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
            version=model.version,
        )
