"""Entidade de domínio: Ordem de Serviço."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoInvalidaError,
    OrdemServicoPagamentoNaoRegistradoError,
    OrdemServicoPossuiItemAReceberError,
    OrdemServicoPossuiServicoSemTempoExecutadoError,
    OrdemServicoTransicaoInvalidaError,
)
from app.shared.value_objects.id import ID


class StatusOrdemServico(str, Enum):
    """Status possíveis de uma Ordem de Serviço."""

    # Etapa 1 — implementada
    RECEBIDA = "RECEBIDA"
    EM_DIAGNOSTICO = "EM_DIAGNOSTICO"
    DIAGNOSTICO_CONCLUIDO = "DIAGNOSTICO_CONCLUIDO"

    # Etapas futuras — declaradas para preparar o modelo
    AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO"
    AGUARDANDO_ITENS = "AGUARDANDO_ITENS"
    APROVADA = "APROVADA"
    EM_EXECUCAO = "EM_EXECUCAO"
    FINALIZADA = "FINALIZADA"
    ENTREGUE = "ENTREGUE"
    ENCERRADA = "ENCERRADA"


class StatusOrcamentoFinanceiro(str, Enum):
    """Fatos mínimos do orçamento mantidos como projeção em Ordens."""

    COMUNICADO = "COMUNICADO"
    APROVADO = "APROVADO"
    RECUSADO = "RECUSADO"


# Transições válidas entre status
_TRANSICOES_VALIDAS: dict[StatusOrdemServico, set[StatusOrdemServico]] = {
    StatusOrdemServico.RECEBIDA: {
        StatusOrdemServico.EM_DIAGNOSTICO,
    },
    StatusOrdemServico.EM_DIAGNOSTICO: {
        StatusOrdemServico.DIAGNOSTICO_CONCLUIDO,
    },
    StatusOrdemServico.DIAGNOSTICO_CONCLUIDO: {
        StatusOrdemServico.AGUARDANDO_APROVACAO,
    },
    StatusOrdemServico.AGUARDANDO_APROVACAO: {
        StatusOrdemServico.APROVADA,
        StatusOrdemServico.AGUARDANDO_ITENS,
        StatusOrdemServico.ENCERRADA,
    },
    StatusOrdemServico.AGUARDANDO_ITENS: {
        StatusOrdemServico.APROVADA,
    },
    StatusOrdemServico.APROVADA: {
        StatusOrdemServico.EM_EXECUCAO,
    },
    StatusOrdemServico.EM_EXECUCAO: {
        StatusOrdemServico.FINALIZADA,
    },
    StatusOrdemServico.FINALIZADA: {
        StatusOrdemServico.ENTREGUE,
    },
    StatusOrdemServico.ENTREGUE: set(),   # estado terminal — conclusão bem-sucedida
    StatusOrdemServico.ENCERRADA: set(),  # estado terminal — orçamento negado/cancelamento
}


@dataclass(frozen=True, kw_only=True)
class OrdemServico:
    """Entidade de domínio representando uma Ordem de Serviço."""

    id: ID
    cliente_id: UUID
    veiculo_id: UUID
    status: StatusOrdemServico
    queixa_inicial: str
    diagnostico: str | None
    criado_em: datetime
    atualizado_em: datetime
    iniciado_diagnostico_em: datetime | None
    diagnostico_concluido_em: datetime | None
    version: int = 1

    # Projeção mínima dos fatos de orçamento; Financeiro segue como autoridade.
    quote_id: UUID | None = None
    quote_status: StatusOrcamentoFinanceiro | None = None
    quote_version: int | None = None
    quote_total: Decimal | None = None
    quote_communicated_at: datetime | None = None
    quote_decided_at: datetime | None = None
    quote_rejection_reason: str | None = None

    # Projeção mínima derivada de Financeiro; não é o agregado de pagamento.
    payment_id: UUID | None = None
    payment_status: str | None = None
    payment_amount: Decimal | None = None
    payment_confirmed_at: datetime | None = None
    payment_quote_id: UUID | None = None
    payment_quote_version: int | None = None

    def __post_init__(self) -> None:
        if not self.queixa_inicial or not self.queixa_inicial.strip():
            raise OrdemServicoInvalidaError("A queixa inicial não pode ser vazia.")
        if self.version < 1:
            raise OrdemServicoInvalidaError("A versão da OS deve ser positiva.")

    def pode_transitar_para(self, novo_status: StatusOrdemServico) -> bool:
        """Verifica se a transição de status é válida."""
        return novo_status in _TRANSICOES_VALIDAS.get(self.status, set())

    def validar_transicao(self, novo_status: StatusOrdemServico) -> None:
        """Lança exceção se a transição de status não for permitida."""
        if not self.pode_transitar_para(novo_status):
            raise OrdemServicoTransicaoInvalidaError(
                f"Não é possível alterar o status de '{self.status.value}' "
                f"para '{novo_status.value}'."
            )

    def registrar_alteracao(self) -> "OrdemServico":
        """Avança os metadados do agregado após alteração em um filho local."""

        return replace(
            self,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )

    def iniciar_diagnostico(self) -> "OrdemServico":
        self.validar_transicao(StatusOrdemServico.EM_DIAGNOSTICO)
        agora = datetime.now(UTC)
        return replace(
            self,
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            atualizado_em=agora,
            iniciado_diagnostico_em=agora,
            version=self.version + 1,
        )

    def registrar_diagnostico(self, diagnostico: str) -> "OrdemServico":
        if self.status != StatusOrdemServico.EM_DIAGNOSTICO:
            raise OrdemServicoTransicaoInvalidaError(
                "Só é possível registrar diagnóstico em OS com status "
                f"'EM_DIAGNOSTICO'. Status atual: '{self.status.value}'."
            )
        if not diagnostico or not diagnostico.strip():
            raise OrdemServicoInvalidaError("O diagnóstico não pode ser vazio.")
        return replace(
            self,
            diagnostico=diagnostico,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )

    def concluir_diagnostico(self) -> "OrdemServico":
        if self.status != StatusOrdemServico.EM_DIAGNOSTICO:
            raise OrdemServicoTransicaoInvalidaError(
                "Só é possível concluir diagnóstico de OS com status "
                f"'EM_DIAGNOSTICO'. Status atual: '{self.status.value}'."
            )
        if not self.diagnostico or not self.diagnostico.strip():
            raise OrdemServicoInvalidaError(
                "Não é possível concluir o diagnóstico sem registrar o texto do diagnóstico."
            )
        agora = datetime.now(UTC)
        return replace(
            self,
            status=StatusOrdemServico.DIAGNOSTICO_CONCLUIDO,
            atualizado_em=agora,
            diagnostico_concluido_em=agora,
            version=self.version + 1,
        )

    def registrar_recebimento_item(self, *, ainda_tem_item_a_receber: bool) -> "OrdemServico":
        if self.status != StatusOrdemServico.AGUARDANDO_ITENS:
            raise OrdemServicoTransicaoInvalidaError(
                "Só é possível confirmar recebimento de item para OS com status "
                f"'AGUARDANDO_ITENS'. Status atual: '{self.status.value}'."
            )
        novo_status = (
            StatusOrdemServico.AGUARDANDO_ITENS
            if ainda_tem_item_a_receber
            else StatusOrdemServico.APROVADA
        )
        return replace(
            self,
            status=novo_status,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )

    def registrar_orcamento_comunicado(
        self,
        *,
        quote_id: UUID,
        quote_version: int,
        total: Decimal,
        communicated_at: datetime,
    ) -> "OrdemServico":
        """Aplica o fato comunicado sem regredir uma decisão financeira posterior."""

        self._validar_fato_orcamento(
            quote_id=quote_id,
            quote_version=quote_version,
            occurred_at=communicated_at,
        )
        if total < Decimal("0"):
            raise OrdemServicoInvalidaError("O total comunicado não pode ser negativo.")
        if self.quote_version is not None:
            if quote_version < self.quote_version:
                return self
            if quote_version == self.quote_version:
                if self.quote_status is StatusOrcamentoFinanceiro.COMUNICADO:
                    if self.quote_total != total:
                        raise OrdemServicoInvalidaError(
                            "A mesma versão do orçamento possui total divergente."
                        )
                    return self
                raise OrdemServicoInvalidaError(
                    "A comunicação contradiz uma decisão financeira da mesma versão."
                )
            if self.quote_status in {
                StatusOrcamentoFinanceiro.APROVADO,
                StatusOrcamentoFinanceiro.RECUSADO,
            }:
                raise OrdemServicoInvalidaError(
                    "Uma comunicação posterior não pode substituir decisão financeira."
                )

        if self.status is StatusOrdemServico.DIAGNOSTICO_CONCLUIDO:
            status = StatusOrdemServico.AGUARDANDO_APROVACAO
        elif self.status in {
            StatusOrdemServico.AGUARDANDO_APROVACAO,
            StatusOrdemServico.AGUARDANDO_ITENS,
            StatusOrdemServico.APROVADA,
            StatusOrdemServico.EM_EXECUCAO,
            StatusOrdemServico.FINALIZADA,
            StatusOrdemServico.ENTREGUE,
        }:
            status = self.status
        else:
            raise OrdemServicoTransicaoInvalidaError(
                f"OS em '{self.status.value}' não aceita orçamento comunicado."
            )
        return replace(
            self,
            status=status,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
            quote_id=quote_id,
            quote_status=StatusOrcamentoFinanceiro.COMUNICADO,
            quote_version=quote_version,
            quote_total=total,
            quote_communicated_at=communicated_at,
            quote_decided_at=None,
        )

    def registrar_orcamento_aprovado(
        self,
        *,
        quote_id: UUID,
        quote_version: int,
        approved_at: datetime,
        possui_item_a_receber: bool,
    ) -> "OrdemServico":
        """Registra a aprovação autoritativa respeitando versão e avanço da OS."""

        self._validar_fato_orcamento(
            quote_id=quote_id,
            quote_version=quote_version,
            occurred_at=approved_at,
        )
        if self.quote_version is not None:
            if quote_version < self.quote_version:
                return self
            if quote_version == self.quote_version:
                if self.quote_status is StatusOrcamentoFinanceiro.APROVADO:
                    return self
                raise OrdemServicoInvalidaError(
                    "A decisão financeira contradiz a versão já projetada."
                )

        if self.status in {
            StatusOrdemServico.DIAGNOSTICO_CONCLUIDO,
            StatusOrdemServico.AGUARDANDO_APROVACAO,
        }:
            status = (
                StatusOrdemServico.AGUARDANDO_ITENS
                if possui_item_a_receber
                else StatusOrdemServico.APROVADA
            )
        elif self.status in {
            StatusOrdemServico.AGUARDANDO_ITENS,
            StatusOrdemServico.APROVADA,
            StatusOrdemServico.EM_EXECUCAO,
            StatusOrdemServico.FINALIZADA,
            StatusOrdemServico.ENTREGUE,
        }:
            status = self.status
        else:
            raise OrdemServicoTransicaoInvalidaError(
                f"OS em '{self.status.value}' não aceita orçamento aprovado."
            )
        return replace(
            self,
            status=status,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
            quote_id=quote_id,
            quote_status=StatusOrcamentoFinanceiro.APROVADO,
            quote_version=quote_version,
            quote_decided_at=approved_at,
            quote_rejection_reason=None,
        )

    def registrar_orcamento_recusado(
        self,
        *,
        quote_id: UUID,
        quote_version: int,
        reason: str | None,
        rejected_at: datetime,
    ) -> "OrdemServico":
        """Registra a recusa sem antecipar a liberação física das reservas."""

        self._validar_fato_orcamento(
            quote_id=quote_id,
            quote_version=quote_version,
            occurred_at=rejected_at,
        )
        normalized_reason = reason
        if normalized_reason is not None and len(normalized_reason) > 512:
            raise OrdemServicoInvalidaError("O motivo da recusa excede 512 caracteres.")
        if self.quote_version is not None:
            if quote_version < self.quote_version:
                return self
            if quote_version == self.quote_version:
                if self.quote_status is StatusOrcamentoFinanceiro.RECUSADO:
                    if self.quote_rejection_reason != normalized_reason:
                        raise OrdemServicoInvalidaError(
                            "A mesma recusa possui motivo divergente."
                        )
                    return self
                raise OrdemServicoInvalidaError(
                    "A decisão financeira contradiz a versão já projetada."
                )

        if self.status is StatusOrdemServico.DIAGNOSTICO_CONCLUIDO:
            status = StatusOrdemServico.AGUARDANDO_APROVACAO
        elif self.status is StatusOrdemServico.AGUARDANDO_APROVACAO:
            status = self.status
        else:
            raise OrdemServicoTransicaoInvalidaError(
                f"OS em '{self.status.value}' não aceita orçamento recusado."
            )
        return replace(
            self,
            status=status,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
            quote_id=quote_id,
            quote_status=StatusOrcamentoFinanceiro.RECUSADO,
            quote_version=quote_version,
            quote_decided_at=rejected_at,
            quote_rejection_reason=normalized_reason,
        )

    def encerrar_por_recusa_financeira(self) -> "OrdemServico":
        """Encerra somente depois que toda limpeza física foi confirmada."""

        if (
            self.quote_status is not StatusOrcamentoFinanceiro.RECUSADO
            or self.status is not StatusOrdemServico.AGUARDANDO_APROVACAO
        ):
            raise OrdemServicoTransicaoInvalidaError(
                "A OS só pode encerrar após recusa financeira registrada."
            )
        return replace(
            self,
            status=StatusOrdemServico.ENCERRADA,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )

    def _validar_fato_orcamento(
        self,
        *,
        quote_id: UUID,
        quote_version: int,
        occurred_at: datetime,
    ) -> None:
        if quote_version < 1:
            raise OrdemServicoInvalidaError("A versão do orçamento deve ser positiva.")
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise OrdemServicoInvalidaError("O fato financeiro deve possuir timezone.")
        if self.quote_id is not None and self.quote_id != quote_id:
            raise OrdemServicoInvalidaError(
                "O fato financeiro pertence a outro orçamento da OS."
            )

    def iniciar_execucao(
        self,
        itens_ativos_status: list[str],
    ) -> "OrdemServico":
        """Inicia execução da OS (APROVADA -> EM_EXECUCAO).

        Recebe lista de status dos itens ativos para validação.
        Lança OrdemServicoTransicaoInvalidaError se OS não estiver APROVADA.
        Lança OrdemServicoPossuiItemAReceberError se houver item ativo A_RECEBER.
        """
        if self.status != StatusOrdemServico.APROVADA:
            raise OrdemServicoTransicaoInvalidaError(
                f"Só é possível iniciar execução de OS com status 'APROVADA'. "
                f"Status atual: '{self.status.value}'."
            )
        if "A_RECEBER" in itens_ativos_status:
            raise OrdemServicoPossuiItemAReceberError(
                "Não é possível iniciar execução enquanto houver item ativo com status 'A_RECEBER'."
            )
        return replace(
            self,
            status=StatusOrdemServico.EM_EXECUCAO,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )

    def finalizar(
        self,
        servicos_ativos_com_tempo: list[bool],
    ) -> "OrdemServico":
        """Finaliza a OS (EM_EXECUCAO -> FINALIZADA).

        Recebe lista de booleans indicando se cada serviço ativo possui tempo_executado_minutos.
        Lança OrdemServicoTransicaoInvalidaError se OS não estiver EM_EXECUCAO.
        Lança OrdemServicoPossuiServicoSemTempoExecutadoError se algum serviço ativo não tiver tempo.
        """
        if self.status != StatusOrdemServico.EM_EXECUCAO:
            raise OrdemServicoTransicaoInvalidaError(
                f"Só é possível finalizar OS com status 'EM_EXECUCAO'. "
                f"Status atual: '{self.status.value}'."
            )
        if not all(servicos_ativos_com_tempo):
            raise OrdemServicoPossuiServicoSemTempoExecutadoError(
                "Não é possível finalizar OS enquanto houver serviço ativo sem tempo executado."
            )
        return replace(
            self,
            status=StatusOrdemServico.FINALIZADA,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )

    def com_pagamento_confirmado(
        self,
        *,
        payment_id: UUID,
        quote_id: UUID,
        amount: Decimal,
        confirmed_at: datetime,
        quote_version: int,
    ) -> "OrdemServico":
        """Atualiza somente a projeção usada na entrega."""

        if amount <= Decimal("0") or quote_version < 1:
            raise OrdemServicoInvalidaError("Projeção financeira inválida.")
        if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
            raise OrdemServicoInvalidaError(
                "A confirmação do pagamento deve possuir timezone."
            )
        if self.status not in {
            StatusOrdemServico.FINALIZADA,
            StatusOrdemServico.ENTREGUE,
        }:
            raise OrdemServicoTransicaoInvalidaError(
                f"OS em '{self.status.value}' não aceita pagamento confirmado."
            )
        if self.quote_id is not None and self.quote_id != quote_id:
            raise OrdemServicoInvalidaError(
                "O pagamento pertence a outro orçamento da OS."
            )
        if self.quote_version is not None and self.quote_version > quote_version:
            raise OrdemServicoInvalidaError(
                "O pagamento referencia versão anterior do orçamento."
            )
        if self.quote_status is StatusOrcamentoFinanceiro.RECUSADO:
            raise OrdemServicoInvalidaError(
                "Pagamento confirmado não pode referenciar orçamento recusado."
            )
        if self.quote_total is not None and amount < self.quote_total:
            raise OrdemServicoInvalidaError(
                "O pagamento confirmado é menor que o orçamento projetado."
            )
        if self.payment_id is not None:
            if (
                self.payment_id == payment_id
                and self.payment_status == "CONFIRMED"
                and self.payment_quote_id == quote_id
                and self.payment_quote_version == quote_version
                and self.payment_amount == amount
                and self.payment_confirmed_at == confirmed_at
            ):
                return self
            raise OrdemServicoInvalidaError(
                "A OS já possui outro pagamento confirmado."
            )
        return replace(
            self,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
            quote_id=quote_id,
            quote_status=StatusOrcamentoFinanceiro.APROVADO,
            quote_version=quote_version,
            payment_id=payment_id,
            payment_status="CONFIRMED",
            payment_amount=amount,
            payment_confirmed_at=confirmed_at,
            payment_quote_id=quote_id,
            payment_quote_version=quote_version,
        )

    def entregar(self) -> "OrdemServico":
        """Entrega a OS (FINALIZADA -> ENTREGUE).

        Lança OrdemServicoTransicaoInvalidaError se OS não estiver FINALIZADA.
        Lança OrdemServicoPagamentoNaoRegistradoError se pagamento não foi registrado.
        """
        if self.status != StatusOrdemServico.FINALIZADA:
            raise OrdemServicoTransicaoInvalidaError(
                f"Só é possível entregar OS com status 'FINALIZADA'. "
                f"Status atual: '{self.status.value}'."
            )
        if self.payment_status != "CONFIRMED" or self.payment_id is None:
            raise OrdemServicoPagamentoNaoRegistradoError(
                "Não é possível entregar OS sem pagamento confirmado."
            )
        return replace(
            self,
            status=StatusOrdemServico.ENTREGUE,
            atualizado_em=datetime.now(UTC),
            version=self.version + 1,
        )
