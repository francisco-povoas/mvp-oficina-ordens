"""Aplica fatos financeiros autoritativos à projeção mínima de Ordens."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.domain.exceptions import FinancialEventInvalidError
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.shared.value_objects.id import ID


class FinancialEventHandler:
    """Usa a transação já aberta pela Inbox para projeção e estado da OS."""

    _SUPPORTED_TYPES = {"QuoteCommunicated", "QuoteApproved"}

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
        else:
            items = await repo.listar_itens_da_os(ID(order_id))
            updated = order.registrar_orcamento_aprovado(
                quote_id=quote_id,
                quote_version=quote_version,
                approved_at=self._timestamp(payload, "approved_at"),
                possui_item_a_receber=any(
                    item.status is StatusItemNaOS.A_RECEBER for item in items
                ),
            )
        if updated != order:
            await repo.atualizar(updated)
        await self._after_effects(message_type, session)

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
