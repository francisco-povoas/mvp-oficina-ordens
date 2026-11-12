"""Exceções de domínio do módulo Ordens de Serviço."""

from app.shared.exceptions import DomainException


class OrdemServicoNaoEncontradaError(DomainException):
    """Lançada quando a OS solicitada não é encontrada."""
    pass


class OrdemServicoInvalidaError(DomainException):
    """Lançada quando os dados da OS são inválidos."""
    pass


class OrdemServicoTransicaoInvalidaError(DomainException):
    """Lançada quando uma transição de status inválida é solicitada."""
    pass


class OrdemServicoConflitoDeVersaoError(DomainException):
    """Lançada quando outra transação alterou a mesma OS primeiro."""

    pass


class OrdemServicoServicoNaoEncontradoError(DomainException):
    """Lançada quando o vínculo de serviço na OS não é encontrado."""
    pass


class OrdemServicoItemNaoEncontradoError(DomainException):
    """Lançada quando o vínculo de item na OS não é encontrado."""
    pass


class ServicoJaAdicionadoNaOrdemServicoError(DomainException):
    """Lançada quando o serviço já está ativo na OS."""
    pass


class ItemJaAdicionadoNaOrdemServicoError(DomainException):
    """Lançada quando o item de estoque já está ativo na OS."""
    pass


class ItemOrdemServicoStatusInvalidoError(DomainException):
    """Lançada quando o item da OS não está no status esperado para a operação."""
    pass


class OrdemServicoPossuiItemAReceberError(DomainException):
    """Lançada quando a OS possui item ativo com status A_RECEBER impedindo a execução."""
    pass


class OrdemServicoServicoCanceladoError(DomainException):
    """Lançada quando se tenta operar em um serviço cancelado da OS."""
    pass


class TempoExecutadoInvalidoError(DomainException):
    """Lançada quando o tempo executado informado é inválido."""
    pass


class OrdemServicoPossuiServicoSemTempoExecutadoError(DomainException):
    """Lançada quando a OS possui serviço ativo sem tempo executado registrado."""
    pass


class OrdemServicoPagamentoNaoRegistradoError(DomainException):
    """Lançada quando a projeção de pagamento ainda não confirma a liquidação."""
    pass


class OrdemServicoPossuiItemPendenteError(DomainException):
    """Lançada quando a OS possui item ativo com status A_RECEBER impedindo a entrega."""
    pass


class OrdemServicoPossuiItemReservadoError(DomainException):
    """Lançada quando a OS possui item ativo com status RESERVADO impedindo a entrega."""
    pass


class EstoqueReservadoInsuficienteError(DomainException):
    """Lançada quando a quantidade reservada no estoque é insuficiente para consumir o item."""
    pass


class ReservaItemSagaInvalidaError(DomainException):
    """Lançada quando os dados persistentes da Saga violam suas invariantes."""

    pass


class ReservaItemSagaTransicaoInvalidaError(DomainException):
    """Lançada quando um resultado faria a Saga regredir ou contradizer seu estado."""

    pass


class ReservaItemSagaConflitoDeVersaoError(DomainException):
    """Lançada quando uma atualização concorrente venceu a versão otimista."""

    pass


class ReservaItemSagaIdempotenciaConflitanteError(DomainException):
    """A mesma chave foi reutilizada com uma requisição semanticamente diferente."""

    pass


class ReservaItemSagaNaoEncontradaError(DomainException):
    """A operação de reserva solicitada não pertence à OS informada."""

    pass


class ResultadoReservaItemSagaInvalidoError(DomainException):
    """O resultado recebido não corresponde ao comando pendente da Saga."""

    pass


class DistributedOperationInvalidError(DomainException):
    """A coordenação distribuída viola uma invariante persistente."""

    pass


class DistributedOperationTransitionError(DomainException):
    """Um resultado tentaria contradizer ou reabrir uma operação terminal."""

    pass


class DistributedOperationVersionConflictError(DomainException):
    """Outra transação atualizou a coordenação primeiro."""

    pass


class DistributedOperationIdempotencyConflictError(DomainException):
    """A mesma chave semântica foi reutilizada com payload divergente."""

    pass

