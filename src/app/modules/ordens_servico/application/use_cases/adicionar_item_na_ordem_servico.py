"""Use case: adicionar item à OS após decisão autoritativa de Recursos."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.integrations.resources import (
    ItemEstoqueNaoEncontradoError,
    RecursosOperacoesPort,
    ReservaItemStatus,
    ReservarItemCommand,
)
from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    AdicionarItemNaOSRequest,
    OrdemServicoItemResponse,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import StatusOrdemServico
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.exceptions import (
    ItemJaAdicionadoNaOrdemServicoError,
    OrdemServicoInvalidaError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoTransicaoInvalidaError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


@dataclass(frozen=True)
class AdicionarItemNaOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork
    recursos: RecursosOperacoesPort

    async def execute(
        self, ordem_servico_id: str, dto: AdicionarItemNaOSRequest
    ) -> OrdemServicoItemResponse:
        if dto.quantidade <= 0:
            raise OrdemServicoInvalidaError("A quantidade deve ser maior que zero.")

        os_id = ID.from_string(ordem_servico_id)
        recurso_id = ID.from_string(dto.item_estoque_id)

        async with self.uow:
            ordem = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if not ordem:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )
            if ordem.status != StatusOrdemServico.EM_DIAGNOSTICO:
                raise OrdemServicoTransicaoInvalidaError(
                    "Só é possível adicionar itens em OS com status "
                    f"'EM_DIAGNOSTICO'. Status atual: '{ordem.status.value}'."
                )

            existente = await self.uow.ordem_servico_repo.obter_item_ativo_por_item_estoque_id(
                os_id, recurso_id
            )
            if existente:
                raise ItemJaAdicionadoNaOrdemServicoError(
                    "Item já foi adicionado a esta ordem de serviço."
                )

            ordem_item_id = ID.generate()
            command = ReservarItemCommand(
                operation_id=uuid4(),
                ordem_servico_id=ordem.id.value,
                ordem_servico_item_id=ordem_item_id.value,
                recurso_id=recurso_id.value,
                quantidade=dto.quantidade,
            )
            resultado = await self.recursos.reservar_item(command)
            if (
                resultado.operation_id != command.operation_id
                or resultado.recurso_id != command.recurso_id
                or resultado.recurso.id != recurso_id
                or resultado.quantidade != command.quantidade
            ):
                raise ItemEstoqueNaoEncontradoError(
                    "Recursos devolveu resultado incompatível com a reserva solicitada."
                )

            status_item = (
                StatusItemNaOS.RESERVADO
                if resultado.status == ReservaItemStatus.RESERVADO
                else StatusItemNaOS.A_RECEBER
            )
            os_item = OrdemServicoItem(
                id=ordem_item_id,
                ordem_servico_id=UUID(ordem_servico_id),
                item_estoque_id=UUID(dto.item_estoque_id),
                nome_item=resultado.recurso.nome,
                tipo_item=resultado.recurso.tipo.value,
                quantidade=dto.quantidade,
                valor_unitario=resultado.recurso.valor_unitario,
                status=status_item,
            )

            await self.uow.ordem_servico_repo.salvar_item(os_item)
            await self.uow.ordem_servico_repo.atualizar(ordem.registrar_alteracao())
            logger.info(
                "Resultado de Recursos consolidado para item '%s' na OS '%s': %s",
                dto.item_estoque_id,
                ordem_servico_id,
                status_item.value,
            )

        return OrdemServicoItemResponse(
            id=str(os_item.id),
            item_estoque_id=str(os_item.item_estoque_id),
            nome_item=os_item.nome_item,
            tipo_item=os_item.tipo_item,
            quantidade=os_item.quantidade,
            valor_unitario=os_item.valor_unitario,
            status=os_item.status.value,
        )
