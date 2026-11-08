"""Use case: consolidar na OS um recebimento confirmado por Recursos."""

from dataclasses import dataclass
from uuid import uuid4

from app.integrations.resources import ReceberItemPendenteCommand, RecursosOperacoesPort
from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import OrdemServicoDetalheResponse
from app.modules.ordens_servico.application.use_cases.obter_ordem_servico_por_id import _to_detalhe
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
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
class ConfirmarRecebimentoItemDaOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork
    recursos: RecursosOperacoesPort

    async def execute(
        self,
        ordem_servico_id: str,
        ordem_servico_item_id: str,
    ) -> OrdemServicoDetalheResponse:
        os_id = ID.from_string(ordem_servico_id)
        item_id = ID.from_string(ordem_servico_item_id)

        async with self.uow:
            ordem = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if not ordem:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            if ordem.status != StatusOrdemServico.AGUARDANDO_ITENS:
                raise OrdemServicoTransicaoInvalidaError(
                    "Só é possível confirmar recebimento de item para OS com status "
                    f"'AGUARDANDO_ITENS'. Status atual: '{ordem.status.value}'."
                )

            item = await self.uow.ordem_servico_repo.obter_item_por_id(item_id)
            if not item or str(item.ordem_servico_id) != ordem_servico_id:
                raise OrdemServicoItemNaoEncontradoError(
                    "Item informado não pertence à ordem de serviço."
                )
            item_reservado = item.confirmar_recebimento()

            command = ReceberItemPendenteCommand(
                operation_id=uuid4(),
                ordem_servico_id=ordem.id.value,
                ordem_servico_item_id=item.id.value,
                recurso_id=item.item_estoque_id,
                quantidade=item.quantidade,
            )
            resultado = await self.recursos.receber_item_pendente(command)
            if (
                resultado.operation_id != command.operation_id
                or resultado.recurso_id != command.recurso_id
                or resultado.quantidade != command.quantidade
            ):
                raise OrdemServicoInvalidaError(
                    "Recursos devolveu resultado incompatível com o recebimento solicitado."
                )

            await self.uow.ordem_servico_repo.atualizar_item(item_reservado)
            itens = await self.uow.ordem_servico_repo.listar_itens_da_os(os_id)
            itens_atualizados = [
                item_reservado if i.id == item.id else i
                for i in itens
            ]
            tem_a_receber = any(
                i.status == StatusItemNaOS.A_RECEBER
                for i in itens_atualizados
                if i.status != StatusItemNaOS.CANCELADO
            )
            os_atualizada = ordem.registrar_recebimento_item(
                ainda_tem_item_a_receber=tem_a_receber
            )
            await self.uow.ordem_servico_repo.atualizar(os_atualizada)
            servicos = await self.uow.ordem_servico_repo.listar_servicos_da_os(os_id)
            await self.uow.commit()

            logger.info(
                "Recebimento do item '%s' consolidado na OS '%s': %s",
                ordem_servico_item_id,
                ordem_servico_id,
                os_atualizada.status.value,
            )

        return _to_detalhe(os_atualizada, servicos, itens_atualizados)
