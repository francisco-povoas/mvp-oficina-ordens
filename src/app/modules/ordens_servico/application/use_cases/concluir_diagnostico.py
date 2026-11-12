"""Use case: Concluir Diagnóstico de uma Ordem de Serviço."""

from dataclasses import dataclass

from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import OrdemServicoResumoResponse
from app.modules.ordens_servico.domain.entities.ordem_servico import OrdemServico
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoNaoEncontradaError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


@dataclass(frozen=True)
class ConcluirDiagnosticoOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork

    async def execute(self, ordem_servico_id: str) -> OrdemServicoResumoResponse:
        _id = ID.from_string(ordem_servico_id)

        async with self.uow:
            os = await self.uow.ordem_servico_repo.obter_por_id(_id)
            if not os:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )

            sagas_pendentes = (
                await self.uow.reserva_item_saga_repo.listar_nao_terminais_da_os(
                    _id.value
                )
            )
            if sagas_pendentes:
                raise OrdemServicoInvalidaError(
                    "Não é possível concluir o diagnóstico enquanto houver "
                    "reserva de item pendente."
                )

            os_atualizada = os.concluir_diagnostico()

            # Validar que há pelo menos um serviço ativo (não cancelado)
            servicos = await self.uow.ordem_servico_repo.listar_servicos_da_os(_id)
            servicos_ativos = [s for s in servicos if not s.cancelado]
            if not servicos_ativos:
                raise OrdemServicoInvalidaError(
                    "Não é possível concluir o diagnóstico sem pelo menos um serviço ativo vinculado."
                )

            await self.uow.ordem_servico_repo.atualizar(os_atualizada)
            logger.info(f"Diagnóstico da OS '{ordem_servico_id}' concluído")

        return _to_resumo(os_atualizada)


def _to_resumo(os: OrdemServico) -> OrdemServicoResumoResponse:
    return OrdemServicoResumoResponse(
        id=str(os.id),
        cliente_id=str(os.cliente_id),
        veiculo_id=str(os.veiculo_id),
        status=os.status.value,
        queixa_inicial=os.queixa_inicial,
        diagnostico=os.diagnostico,
        criado_em=os.criado_em,
        atualizado_em=os.atualizado_em,
        iniciado_diagnostico_em=os.iniciado_diagnostico_em,
        diagnostico_concluido_em=os.diagnostico_concluido_em,
    )
