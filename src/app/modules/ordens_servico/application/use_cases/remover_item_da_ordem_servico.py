"""Use case: cancelar item da OS após liberação autoritativa em Recursos."""

from dataclasses import dataclass
from uuid import uuid4

from app.integrations.resources import LiberarReservaCommand, RecursosOperacoesPort
from app.logger import setup_logger
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoItemNaoEncontradoError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoTransicaoInvalidaError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


@dataclass(frozen=True)
class RemoverItemDaOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork
    recursos: RecursosOperacoesPort

    async def execute(self, ordem_servico_id: str, ordem_servico_item_id: str) -> None:
        os_id = ID.from_string(ordem_servico_id)
        osi_id = ID.from_string(ordem_servico_item_id)

        async with self.uow:
            ordem = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if not ordem:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            if ordem.status != StatusOrdemServico.EM_DIAGNOSTICO:
                raise OrdemServicoTransicaoInvalidaError(
                    "Só é possível remover itens em OS com status "
                    f"'EM_DIAGNOSTICO'. Status atual: '{ordem.status.value}'."
                )

            os_item = await self.uow.ordem_servico_repo.obter_item_por_id(osi_id)
            if not os_item or str(os_item.ordem_servico_id) != ordem_servico_id:
                raise OrdemServicoItemNaoEncontradoError(
                    f"Item com ID {ordem_servico_item_id} não encontrado na OS."
                )
            if os_item.status == StatusItemNaOS.CANCELADO:
                raise OrdemServicoInvalidaError("O item já foi cancelado.")

            if os_item.status == StatusItemNaOS.RESERVADO:
                command = LiberarReservaCommand(
                    operation_id=uuid4(),
                    ordem_servico_id=ordem.id.value,
                    ordem_servico_item_id=os_item.id.value,
                    recurso_id=os_item.item_estoque_id,
                    quantidade=os_item.quantidade,
                )
                resultado = await self.recursos.liberar_reserva(command)
                if (
                    resultado.operation_id != command.operation_id
                    or resultado.recurso_id != command.recurso_id
                    or resultado.quantidade != command.quantidade
                ):
                    raise OrdemServicoInvalidaError(
                        "Recursos devolveu resultado incompatível com a liberação solicitada."
                    )

            os_item_cancelado = OrdemServicoItem(
                id=os_item.id,
                ordem_servico_id=os_item.ordem_servico_id,
                item_estoque_id=os_item.item_estoque_id,
                nome_item=os_item.nome_item,
                tipo_item=os_item.tipo_item,
                quantidade=os_item.quantidade,
                valor_unitario=os_item.valor_unitario,
                status=StatusItemNaOS.CANCELADO,
            )
            await self.uow.ordem_servico_repo.atualizar_item(os_item_cancelado)
            await self.uow.ordem_servico_repo.atualizar(ordem.registrar_alteracao())
            logger.info("Item '%s' cancelado da OS '%s'", ordem_servico_item_id, ordem_servico_id)
