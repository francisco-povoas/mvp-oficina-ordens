"""Aplicação transacional dos resultados de reserva produzidos por Recursos."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    CodigoRejeicaoReservaItemSaga,
    ReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ResultadoReservaItemSagaInvalidoError,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.shared.value_objects.id import ID


class ReservationSagaResultHandler:
    """Despacha resultados v1 usando a sessão já aberta pela Inbox."""

    _SUPPORTED_TYPES = {
        "ResourceReserved",
        "ItemBackordered",
        "ReservationRejected",
    }

    async def handle(
        self, envelope: Mapping[str, object], session: AsyncSession
    ) -> None:
        message_type = str(envelope["message_type"])
        if message_type not in self._SUPPORTED_TYPES:
            raise ResultadoReservaItemSagaInvalidoError(
                f"Handler de resultado de reserva não suporta '{message_type}'."
            )

        operation_id = self._uuid(envelope, "operation_id")
        repo = ReservaItemSagaRepo(session)
        saga = await repo.obter_por_operation_id(operation_id)
        if saga is None:
            raise ResultadoReservaItemSagaInvalidoError(
                f"Saga da operação '{operation_id}' não encontrada."
            )
        self._validate_envelope(envelope, saga)

        payload = self._payload(envelope)
        message_id = self._uuid(envelope, "message_id")
        resource_id = self._uuid(payload, "resource_id")
        if resource_id != saga.resource_id:
            raise ResultadoReservaItemSagaInvalidoError(
                "O recurso do resultado não corresponde ao recurso da Saga."
            )

        if message_type == "ResourceReserved":
            updated_saga, item = self._reserved(saga, payload, message_id)
        elif message_type == "ItemBackordered":
            updated_saga, item = self._backordered(saga, payload, message_id)
        else:
            updated_saga = self._rejected(saga, payload, message_id)
            item = None

        if item is not None:
            order_repo = OrdemServicoRepo(session)
            order = await order_repo.obter_por_id(ID(saga.ordem_servico_id))
            if order is None:
                raise ResultadoReservaItemSagaInvalidoError(
                    f"OS '{saga.ordem_servico_id}' da Saga não encontrada."
                )
            if await order_repo.obter_item_por_id(item.id) is not None:
                raise ResultadoReservaItemSagaInvalidoError(
                    f"Item '{item.id}' da Saga já foi materializado."
                )
            await order_repo.salvar_item(item)
            await order_repo.atualizar(order.registrar_alteracao())

        await repo.atualizar(updated_saga)
        await self._after_effects(message_type, session)

    async def _after_effects(
        self, message_type: str, session: AsyncSession
    ) -> None:
        """Ponto de falha controlada para provar rollback transacional em testes."""

    def _reserved(
        self,
        saga: ReservaItemSaga,
        payload: Mapping[str, object],
        message_id: UUID,
    ) -> tuple[ReservaItemSaga, OrdemServicoItem]:
        quantity = self._positive_int(payload, "quantity")
        self._require_quantity(saga, quantity)
        snapshot = self._snapshot(payload)
        updated = saga.registrar_reserva(
            reservation_id=self._uuid(payload, "reservation_id"),
            message_id=message_id,
            resultado_em=self._timestamp(payload, "reserved_at"),
            resource_name=snapshot[0],
            resource_description=snapshot[1],
            resource_unit_price=snapshot[2],
        )
        return updated, self._item(saga, snapshot, StatusItemNaOS.RESERVADO)

    def _backordered(
        self,
        saga: ReservaItemSaga,
        payload: Mapping[str, object],
        message_id: UUID,
    ) -> tuple[ReservaItemSaga, OrdemServicoItem]:
        requested_quantity = self._positive_int(payload, "requested_quantity")
        self._require_quantity(saga, requested_quantity)
        available_quantity = self._int(payload, "available_quantity")
        if available_quantity < 0 or available_quantity >= requested_quantity:
            raise ResultadoReservaItemSagaInvalidoError(
                "Backorder exige saldo disponível menor que a quantidade solicitada."
            )
        snapshot = self._snapshot(payload)
        updated = saga.registrar_backorder(
            message_id=message_id,
            resultado_em=self._timestamp(payload, "decided_at"),
            resource_name=snapshot[0],
            resource_description=snapshot[1],
            resource_unit_price=snapshot[2],
        )
        return updated, self._item(saga, snapshot, StatusItemNaOS.A_RECEBER)

    def _rejected(
        self,
        saga: ReservaItemSaga,
        payload: Mapping[str, object],
        message_id: UUID,
    ) -> ReservaItemSaga:
        try:
            code = CodigoRejeicaoReservaItemSaga(str(payload["rejection_code"]))
        except (KeyError, ValueError) as exc:
            raise ResultadoReservaItemSagaInvalidoError(
                "Código de rejeição inválido."
            ) from exc
        detail = str(payload.get("detail", "")).strip()
        if not detail or len(detail) > 512:
            raise ResultadoReservaItemSagaInvalidoError(
                "Detalhe da rejeição deve possuir entre 1 e 512 caracteres."
            )
        return saga.registrar_rejeicao(
            code=code,
            detail=detail,
            message_id=message_id,
            resultado_em=self._timestamp(payload, "rejected_at"),
        )

    @staticmethod
    def _item(
        saga: ReservaItemSaga,
        snapshot: tuple[str, str | None, Decimal],
        status: StatusItemNaOS,
    ) -> OrdemServicoItem:
        return OrdemServicoItem(
            id=ID(saga.ordem_servico_item_id),
            ordem_servico_id=saga.ordem_servico_id,
            item_estoque_id=saga.resource_id,
            nome_item=snapshot[0],
            tipo_item="PECA",
            quantidade=saga.quantity,
            valor_unitario=snapshot[2],
            status=status,
        )

    @classmethod
    def _validate_envelope(
        cls, envelope: Mapping[str, object], saga: ReservaItemSaga
    ) -> None:
        if int(envelope["message_version"]) != 1:
            raise ResultadoReservaItemSagaInvalidoError(
                "Somente resultados de reserva v1 são aceitos."
            )
        if cls._uuid(envelope, "saga_id") != saga.id:
            raise ResultadoReservaItemSagaInvalidoError(
                "O saga_id do resultado não corresponde à operação persistida."
            )
        if cls._uuid(envelope, "correlation_id") != saga.correlation_id:
            raise ResultadoReservaItemSagaInvalidoError(
                "O correlation_id do resultado não corresponde à Saga."
            )
        if cls._uuid(envelope, "causation_id") != saga.ultimo_comando_message_id:
            raise ResultadoReservaItemSagaInvalidoError(
                "O resultado não foi causado pelo comando pendente da Saga."
            )

    @classmethod
    def _snapshot(
        cls, payload: Mapping[str, object]
    ) -> tuple[str, str | None, Decimal]:
        raw = payload.get("resource_snapshot")
        if not isinstance(raw, Mapping):
            raise ResultadoReservaItemSagaInvalidoError(
                "Snapshot autoritativo do recurso ausente."
            )
        name = str(raw.get("name", "")).strip()
        description_value = raw.get("description")
        description = None if description_value is None else str(description_value)
        if not name or len(name) > 200:
            raise ResultadoReservaItemSagaInvalidoError(
                "Nome do snapshot deve possuir entre 1 e 200 caracteres."
            )
        if description is not None and len(description) > 1000:
            raise ResultadoReservaItemSagaInvalidoError(
                "Descrição do snapshot excede 1000 caracteres."
            )
        try:
            price = Decimal(str(raw["unit_price"]))
        except (KeyError, InvalidOperation) as exc:
            raise ResultadoReservaItemSagaInvalidoError(
                "Preço unitário do snapshot é inválido."
            ) from exc
        if price < Decimal("0") or price.as_tuple().exponent != -2:
            raise ResultadoReservaItemSagaInvalidoError(
                "Preço unitário do snapshot deve ser monetário e não negativo."
            )
        return name, description, price

    @staticmethod
    def _payload(envelope: Mapping[str, object]) -> Mapping[str, object]:
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            raise ResultadoReservaItemSagaInvalidoError("Payload do resultado inválido.")
        return payload

    @staticmethod
    def _uuid(source: Mapping[str, object], field: str) -> UUID:
        try:
            return UUID(str(source[field]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ResultadoReservaItemSagaInvalidoError(
                f"Campo UUID '{field}' inválido."
            ) from exc

    @classmethod
    def _positive_int(cls, source: Mapping[str, object], field: str) -> int:
        value = cls._int(source, field)
        if value < 1:
            raise ResultadoReservaItemSagaInvalidoError(
                f"Campo '{field}' deve ser positivo."
            )
        return value

    @staticmethod
    def _int(source: Mapping[str, object], field: str) -> int:
        value = source.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ResultadoReservaItemSagaInvalidoError(
                f"Campo inteiro '{field}' inválido."
            )
        return value

    @staticmethod
    def _timestamp(source: Mapping[str, object], field: str) -> datetime:
        try:
            value = datetime.fromisoformat(str(source[field]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ResultadoReservaItemSagaInvalidoError(
                f"Timestamp '{field}' inválido."
            ) from exc
        if value.tzinfo is None or value.utcoffset() is None:
            raise ResultadoReservaItemSagaInvalidoError(
                f"Timestamp '{field}' deve possuir fuso horário."
            )
        return value.astimezone(UTC)

    @staticmethod
    def _require_quantity(saga: ReservaItemSaga, quantity: int) -> None:
        if quantity != saga.quantity:
            raise ResultadoReservaItemSagaInvalidoError(
                "A quantidade do resultado não corresponde à quantidade da Saga."
            )
