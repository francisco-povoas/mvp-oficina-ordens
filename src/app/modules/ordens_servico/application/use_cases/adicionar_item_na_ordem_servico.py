"""Inicia, de forma idempotente, a Saga assíncrona de inclusão de peça."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.integrations.resources import ItemEstoqueNaoEncontradoError, RecursoCatalogoPort
from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    AdicionarItemNaOSRequest,
    ReservaItemSagaResponse,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.reserva_item_saga import ReservaItemSaga
from app.modules.ordens_servico.domain.exceptions import (
    ItemJaAdicionadoNaOrdemServicoError,
    OrdemServicoInvalidaError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoTransicaoInvalidaError,
    ReservaItemSagaIdempotenciaConflitanteError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.outbox import OutboxMessage
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


def _request_hash(*, ordem_servico_id: UUID, resource_id: UUID, quantity: int) -> str:
    encoded = json.dumps(
        {
            "ordem_servico_id": str(ordem_servico_id),
            "resource_id": str(resource_id),
            "quantity": quantity,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def saga_response(saga: ReservaItemSaga) -> ReservaItemSagaResponse:
    return ReservaItemSagaResponse(
        operation_id=str(saga.operation_id),
        saga_id=str(saga.id),
        ordem_servico_item_id=str(saga.ordem_servico_item_id),
        estado=saga.estado.value,
        terminal_result=saga.terminal_result.value if saga.terminal_result else None,
        rejection_code=saga.rejection_code.value if saga.rejection_code else None,
        reservation_id=str(saga.reservation_id) if saga.reservation_id else None,
        status_url=(
            f"/api/v1/ordens-servico/{saga.ordem_servico_id}/"
            f"itens/operacoes/{saga.operation_id}"
        ),
    )


@dataclass(frozen=True)
class AdicionarItemNaOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork
    recursos: RecursoCatalogoPort

    async def execute(
        self,
        ordem_servico_id: str,
        dto: AdicionarItemNaOSRequest,
        *,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> ReservaItemSagaResponse:
        if dto.quantidade <= 0:
            raise OrdemServicoInvalidaError("A quantidade deve ser maior que zero.")

        os_id = ID.from_string(ordem_servico_id)
        recurso_id = ID.from_string(dto.item_estoque_id)
        payload_hash = _request_hash(
            ordem_servico_id=os_id.value,
            resource_id=recurso_id.value,
            quantity=dto.quantidade,
        )

        async with self.uow:
            ordem = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if not ordem:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )

            existente = await self.uow.reserva_item_saga_repo.obter_por_chave_idempotencia(
                ordem_servico_id=os_id.value,
                idempotency_key=idempotency_key,
            )
            if existente:
                if existente.request_hash != payload_hash:
                    raise ReservaItemSagaIdempotenciaConflitanteError(
                        "A chave de idempotência já foi usada com outro payload."
                    )
                return saga_response(existente)

            if ordem.status != StatusOrdemServico.EM_DIAGNOSTICO:
                raise OrdemServicoTransicaoInvalidaError(
                    "Só é possível adicionar itens em OS com status "
                    f"'EM_DIAGNOSTICO'. Status atual: '{ordem.status.value}'."
                )

            item_existente = (
                await self.uow.ordem_servico_repo.obter_item_ativo_por_item_estoque_id(
                    os_id, recurso_id
                )
            )
            if item_existente:
                raise ItemJaAdicionadoNaOrdemServicoError(
                    "Item já foi adicionado a esta ordem de serviço."
                )

            snapshot = await self.recursos.obter_recurso_por_id(recurso_id)
            if snapshot is None:
                raise ItemEstoqueNaoEncontradoError(
                    f"Item de estoque com ID {recurso_id} não encontrado."
                )

            agora = datetime.now(UTC)
            saga_id = uuid4()
            operation_id = uuid4()
            ordem_item_id = uuid4()
            message_id = uuid4()
            saga = ReservaItemSaga.iniciar(
                saga_id=saga_id,
                operation_id=operation_id,
                ordem_servico_id=os_id.value,
                ordem_servico_item_id=ordem_item_id,
                resource_id=recurso_id.value,
                quantity=dto.quantidade,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                request_hash=payload_hash,
                reserve_message_id=message_id,
                agora=agora,
            )
            await self.uow.reserva_item_saga_repo.salvar(saga)
            await self.uow.outbox.add(
                OutboxMessage(
                    message_id=message_id,
                    aggregate_type="ReservaItemSaga",
                    aggregate_id=operation_id,
                    event_type="ReserveItem.v1",
                    event_version=1,
                    payload={
                        "order_id": str(os_id.value),
                        "order_item_id": str(ordem_item_id),
                        "resource_id": str(recurso_id.value),
                        "quantity": dto.quantidade,
                        "resource_snapshot": {
                            "name": snapshot.nome,
                            "description": snapshot.descricao,
                            "unit_price": format(snapshot.valor_unitario, ".2f"),
                        },
                    },
                    correlation_id=correlation_id,
                    causation_id=None,
                    saga_id=saga_id,
                    occurred_at=agora,
                )
            )
            await self.uow.ordem_servico_repo.atualizar(ordem.registrar_alteracao())
            logger.info(
                "Saga de reserva iniciada operation_id=%s saga_id=%s ordem_id=%s",
                operation_id,
                saga_id,
                os_id.value,
            )

        return saga_response(saga)
