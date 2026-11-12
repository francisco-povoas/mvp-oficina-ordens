"""Aplica fatos financeiros autoritativos à projeção mínima de Ordens."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.distributed_operation import (
    DistributedOperation,
    DistributedOperationState,
    DistributedOperationType,
    distributed_operation_business_key,
    distributed_operation_request_hash,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    ResultadoTerminalReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    FinancialEventInvalidError,
    OrdemServicoInvalidaError,
)
from app.modules.ordens_servico.infrastructure.db.repositories.distributed_operation_repo import (
    PostgresDistributedOperationRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.shared.infra.db.outbox_repository import PostgresOutboxRepository
from app.shared.outbox import OutboxMessage
from app.shared.value_objects.id import ID


class FinancialEventHandler:
    """Usa a transação já aberta pela Inbox para projeção e estado da OS."""

    _SUPPORTED_TYPES = {
        "QuoteCommunicated",
        "QuoteApproved",
        "QuoteRejected",
        "PaymentConfirmed",
    }

    async def handle(
        self, envelope: Mapping[str, object], session: AsyncSession
    ) -> None:
        message_type = str(envelope["message_type"])
        if message_type not in self._SUPPORTED_TYPES:
            raise FinancialEventInvalidError(
                f"Evento financeiro ainda não suportado: '{message_type}'."
            )
        if int(envelope["message_version"]) != 1 or envelope.get("saga_id") is not None:
            raise FinancialEventInvalidError("Somente eventos financeiros v1 sem Saga são aceitos.")
        payload = self._payload(envelope)
        order_id = self._uuid(payload, "order_id")
        quote_id = self._uuid(payload, "quote_id")
        quote_version = self._positive_int(payload, "quote_version")

        repo = OrdemServicoRepo(session)
        order = await repo.obter_por_id_com_lock(ID(order_id))
        if order is None:
            raise FinancialEventInvalidError(f"OS '{order_id}' não encontrada.")

        if message_type == "QuoteCommunicated":
            updated = order.registrar_orcamento_comunicado(
                quote_id=quote_id,
                quote_version=quote_version,
                total=self._money(payload, "total"),
                communicated_at=self._timestamp(payload, "communicated_at"),
            )
        elif message_type == "QuoteApproved":
            items = await repo.listar_itens_da_os(ID(order_id))
            updated = order.registrar_orcamento_aprovado(
                quote_id=quote_id,
                quote_version=quote_version,
                approved_at=self._timestamp(payload, "approved_at"),
                possui_item_a_receber=any(
                    item.status is StatusItemNaOS.A_RECEBER for item in items
                ),
            )
        elif message_type == "QuoteRejected":
            updated, cleanup_complete = await self._reject_quote(
                envelope=envelope,
                payload=payload,
                order=order,
                quote_id=quote_id,
                quote_version=quote_version,
                session=session,
            )
        else:
            if payload.get("status") != "CONFIRMED":
                raise FinancialEventInvalidError(
                    "PaymentConfirmed exige status CONFIRMED."
                )
            updated = order.com_pagamento_confirmado(
                payment_id=self._uuid(payload, "payment_id"),
                quote_id=quote_id,
                amount=self._money(payload, "amount"),
                confirmed_at=self._timestamp(payload, "confirmed_at"),
                quote_version=quote_version,
            )
        if updated != order:
            await repo.atualizar(updated)
            if message_type == "QuoteRejected" and cleanup_complete:
                await repo.atualizar(updated.encerrar_por_recusa_financeira())
        await self._after_effects(message_type, session)

    async def _reject_quote(
        self,
        *,
        envelope: Mapping[str, object],
        payload: Mapping[str, object],
        order,
        quote_id: UUID,
        quote_version: int,
        session: AsyncSession,
    ):
        reason_value = payload.get("reason")
        reason = None if reason_value is None else str(reason_value)
        rejected_at = self._timestamp(payload, "rejected_at")
        rejected = order.registrar_orcamento_recusado(
            quote_id=quote_id,
            quote_version=quote_version,
            reason=reason,
            rejected_at=rejected_at,
        )
        if rejected == order:
            return order, False

        order_repo = OrdemServicoRepo(session)
        operation_repo = PostgresDistributedOperationRepo(session)
        saga_repo = ReservaItemSagaRepo(session)
        pending_cleanup = False
        for saga in await saga_repo.listar_nao_terminais_da_os(order.id.value):
            updated_saga = saga.solicitar_cancelamento(agora=rejected_at)
            if updated_saga.version != saga.version:
                await saga_repo.atualizar(updated_saga)
            pending_cleanup = True

        correlation_id = self._uuid(envelope, "correlation_id")
        causation_id = self._uuid(envelope, "message_id")
        for item in await order_repo.listar_itens_da_os(ID(order.id.value)):
            if item.status is StatusItemNaOS.CANCELADO:
                continue
            if item.status is StatusItemNaOS.A_RECEBER:
                await self._cancel_backorder(
                    order_id=order.id.value,
                    item=item,
                    correlation_id=correlation_id,
                    now=rejected_at,
                    order_repo=order_repo,
                    operation_repo=operation_repo,
                )
                continue
            if item.status is not StatusItemNaOS.RESERVADO:
                raise FinancialEventInvalidError(
                    f"Item em '{item.status.value}' é incompatível com recusa de orçamento."
                )
            await self._release_reservation(
                order_id=order.id.value,
                item=item,
                correlation_id=correlation_id,
                causation_id=causation_id,
                now=rejected_at,
                saga_repo=saga_repo,
                operation_repo=operation_repo,
                outbox=PostgresOutboxRepository(session),
            )
            pending_cleanup = True
        return rejected, not pending_cleanup

    @staticmethod
    async def _cancel_backorder(
        *,
        order_id: UUID,
        item,
        correlation_id: UUID,
        now: datetime,
        order_repo: OrdemServicoRepo,
        operation_repo: PostgresDistributedOperationRepo,
    ) -> None:
        business_key = distributed_operation_business_key(
            DistributedOperationType.RELEASE,
            ordem_servico_item_id=item.id.value,
        )
        request_hash = distributed_operation_request_hash(
            operation_type=DistributedOperationType.RELEASE,
            ordem_servico_id=order_id,
            ordem_servico_item_id=item.id.value,
            resource_id=item.item_estoque_id,
            quantity=item.quantidade,
            reservation_id=None,
        )
        existing = await operation_repo.get_by_business_key(business_key)
        if existing is not None:
            existing.validate_repetition(request_hash)
        else:
            await operation_repo.save(
                DistributedOperation.complete_without_reservation(
                    operation_id=uuid4(),
                    ordem_servico_id=order_id,
                    ordem_servico_item_id=item.id.value,
                    resource_id=item.item_estoque_id,
                    quantity=item.quantidade,
                    business_key=business_key,
                    request_hash=request_hash,
                    correlation_id=correlation_id,
                    now=now,
                )
            )
        await order_repo.atualizar_item(replace(item, status=StatusItemNaOS.CANCELADO))

    async def _release_reservation(
        self,
        *,
        order_id: UUID,
        item,
        correlation_id: UUID,
        causation_id: UUID,
        now: datetime,
        saga_repo: ReservaItemSagaRepo,
        operation_repo: PostgresDistributedOperationRepo,
        outbox: PostgresOutboxRepository,
    ) -> None:
        reservation_id = await self._reservation_id(item, saga_repo, operation_repo)
        business_key = distributed_operation_business_key(
            DistributedOperationType.RELEASE,
            ordem_servico_item_id=item.id.value,
        )
        request_hash = distributed_operation_request_hash(
            operation_type=DistributedOperationType.RELEASE,
            ordem_servico_id=order_id,
            ordem_servico_item_id=item.id.value,
            resource_id=item.item_estoque_id,
            quantity=item.quantidade,
            reservation_id=reservation_id,
        )
        existing = await operation_repo.get_by_business_key(business_key)
        if existing is not None:
            existing.validate_repetition(request_hash)
            if existing.state is DistributedOperationState.COMPLETED:
                raise FinancialEventInvalidError(
                    "Liberação concluída ainda possui item reservado na OS."
                )
            return
        message_id = uuid4()
        operation = DistributedOperation.start(
            operation_id=uuid4(),
            operation_type=DistributedOperationType.RELEASE,
            ordem_servico_id=order_id,
            ordem_servico_item_id=item.id.value,
            resource_id=item.item_estoque_id,
            quantity=item.quantidade,
            business_key=business_key,
            request_hash=request_hash,
            correlation_id=correlation_id,
            command_message_id=message_id,
            reservation_id=reservation_id,
            now=now,
        )
        await operation_repo.save(operation)
        await outbox.add(
            OutboxMessage(
                message_id=message_id,
                aggregate_type="DistributedOperation",
                aggregate_id=operation.operation_id,
                event_type="ReleaseReservation.v1",
                event_version=1,
                payload={
                    "reservation_id": str(reservation_id),
                    "resource_id": str(item.item_estoque_id),
                    "quantity": item.quantidade,
                    "reason": "QUOTE_REJECTION",
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
                saga_id=None,
                occurred_at=now,
            )
        )

    @staticmethod
    async def _reservation_id(item, saga_repo, operation_repo) -> UUID:
        received = await operation_repo.get_latest_completed_for_item(
            item.id.value,
            DistributedOperationType.RECEIVE,
        )
        if received is not None and received.reservation_id is not None:
            return received.reservation_id
        saga = await saga_repo.obter_por_ordem_servico_item_id(item.id.value)
        if (
            saga is not None
            and saga.terminal_result is ResultadoTerminalReservaItemSaga.RESERVED
            and saga.reservation_id is not None
        ):
            return saga.reservation_id
        raise OrdemServicoInvalidaError(
            f"Item '{item.id}' não possui reserva autoritativa para liberação."
        )

    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        """Ponto de falha injetável para comprovar rollback da Inbox."""

    @staticmethod
    def _payload(envelope: Mapping[str, object]) -> Mapping[str, object]:
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise FinancialEventInvalidError("Payload financeiro inválido.")
        return payload

    @staticmethod
    def _uuid(source: Mapping[str, object], field: str) -> UUID:
        try:
            return UUID(str(source[field]))
        except (KeyError, TypeError, ValueError) as exc:
            raise FinancialEventInvalidError(f"Campo UUID '{field}' inválido.") from exc

    @staticmethod
    def _positive_int(source: Mapping[str, object], field: str) -> int:
        value = source.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise FinancialEventInvalidError(f"Campo '{field}' deve ser positivo.")
        return value

    @staticmethod
    def _money(source: Mapping[str, object], field: str) -> Decimal:
        try:
            value = Decimal(str(source[field]))
        except (KeyError, InvalidOperation) as exc:
            raise FinancialEventInvalidError(f"Campo monetário '{field}' inválido.") from exc
        if value < Decimal("0") or value.as_tuple().exponent != -2:
            raise FinancialEventInvalidError(
                f"Campo monetário '{field}' deve possuir duas casas decimais."
            )
        return value

    @staticmethod
    def _timestamp(source: Mapping[str, object], field: str) -> datetime:
        try:
            value = datetime.fromisoformat(str(source[field]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise FinancialEventInvalidError(f"Timestamp '{field}' inválido.") from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise FinancialEventInvalidError(f"Timestamp '{field}' deve possuir timezone.")
        return value.astimezone(UTC)
