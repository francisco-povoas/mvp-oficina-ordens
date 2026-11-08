"""Use case: entregar OS após consumo autoritativo das reservas em Recursos."""

from dataclasses import dataclass
from uuid import uuid4

from app.integrations.finance import FinanceStatusPort
from app.integrations.resources import ConsumirReservaCommand, RecursosOperacoesPort
from app.logger import setup_logger
from app.modules.ordens_servico.application.dtos.ordem_servico import OrdemServicoDetalheResponse
from app.modules.ordens_servico.application.use_cases.obter_ordem_servico_por_id import _to_detalhe
from app.modules.ordens_servico.domain.entities.ordem_servico_item import StatusItemNaOS
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoPagamentoNaoRegistradoError,
    OrdemServicoPossuiItemPendenteError,
    OrdemServicoPossuiItemReservadoError,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import OrdemServicoUnitOfWork
from app.shared.value_objects.id import ID

logger = setup_logger(__name__)


@dataclass(frozen=True)
class EntregarOrdemServicoUseCase:
    uow: OrdemServicoUnitOfWork
    recursos: RecursosOperacoesPort
    financeiro: FinanceStatusPort | None = None

    async def execute(self, ordem_servico_id: str) -> OrdemServicoDetalheResponse:
        os_id = ID.from_string(ordem_servico_id)

        async with self.uow:
            ordem = await self.uow.ordem_servico_repo.obter_por_id(os_id)
            if not ordem:
                raise OrdemServicoNaoEncontradaError(
                    f"OS com ID {ordem_servico_id} não encontrada."
                )

            if ordem.payment_status != "CONFIRMED" or ordem.payment_id is None:
                if self.financeiro is None:
                    raise OrdemServicoPagamentoNaoRegistradoError(
                        "Não é possível entregar OS sem pagamento confirmado."
                    )
                financial_status = await self.financeiro.obter_status(ordem.id.value)
                payment = financial_status.payment if financial_status else None
                quote = financial_status.quote if financial_status else None
                if (
                    payment is None
                    or quote is None
                    or payment.status != "CONFIRMED"
                    or payment.confirmed_at is None
                    or quote.status != "APROVADO"
                ):
                    raise OrdemServicoPagamentoNaoRegistradoError(
                        "Não é possível entregar OS sem confirmação financeira válida."
                    )
                ordem = ordem.com_pagamento_confirmado(
                    payment_id=payment.payment_id,
                    amount=payment.amount,
                    confirmed_at=payment.confirmed_at,
                    quote_version=quote.quote_version,
                )
                await self.uow.ordem_servico_repo.atualizar(ordem)

            os_entregue = ordem.entregar()
            itens = await self.uow.ordem_servico_repo.listar_itens_da_os(os_id)
            for item in itens:
                if item.status == StatusItemNaOS.A_RECEBER:
                    raise OrdemServicoPossuiItemPendenteError(
                        "Não é possível entregar OS com item ativo em status "
                        f"'{item.status.value}'."
                    )
                if item.status == StatusItemNaOS.RESERVADO:
                    raise OrdemServicoPossuiItemReservadoError(
                        "Não é possível entregar OS com item ativo em status "
                        f"'{item.status.value}'."
                    )

            itens_atualizados = []
            itens_consumidos = []
            for item in itens:
                if item.status != StatusItemNaOS.EM_USO:
                    itens_atualizados.append(item)
                    continue

                command = ConsumirReservaCommand(
                    operation_id=uuid4(),
                    ordem_servico_id=ordem.id.value,
                    ordem_servico_item_id=item.id.value,
                    recurso_id=item.item_estoque_id,
                    quantidade=item.quantidade,
                )
                resultado = await self.recursos.consumir_reserva(command)
                if (
                    resultado.operation_id != command.operation_id
                    or resultado.recurso_id != command.recurso_id
                    or resultado.quantidade != command.quantidade
                ):
                    raise OrdemServicoInvalidaError(
                        "Recursos devolveu resultado incompatível com o consumo solicitado."
                    )
                item_consumido = item.consumir()
                itens_atualizados.append(item_consumido)
                itens_consumidos.append(item_consumido)

            for item in itens_consumidos:
                await self.uow.ordem_servico_repo.atualizar_item(item)
            await self.uow.ordem_servico_repo.atualizar(os_entregue)
            await self.uow.commit()
            servicos = await self.uow.ordem_servico_repo.listar_servicos_da_os(os_id)

            logger.info("OS '%s' entregue com consumos confirmados.", ordem_servico_id)
            return _to_detalhe(os_entregue, servicos, itens_atualizados)
