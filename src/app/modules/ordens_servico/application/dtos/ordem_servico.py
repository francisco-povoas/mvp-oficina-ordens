"""DTOs (request/response) do módulo Ordens de Serviço."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CriarOrdemServicoRequest:
    cliente_id: str
    veiculo_id: str
    queixa_inicial: str


@dataclass(frozen=True, kw_only=True)
class RegistrarDiagnosticoRequest:
    diagnostico: str


@dataclass(frozen=True, kw_only=True)
class AdicionarServicoNaOSRequest:
    servico_id: str
    observacao: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AdicionarItemNaOSRequest:
    item_estoque_id: str
    quantidade: int


@dataclass(frozen=True, kw_only=True)
class RegistrarTempoExecutadoServicoRequest:
    """Body para registrar tempo executado em serviço da OS."""
    tempo_executado_minutos: int


# ---------------------------------------------------------------------------
# Response DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class OrdemServicoServicoResponse:
    id: str
    servico_id: str
    nome_servico: str
    descricao_servico: Optional[str]
    valor_unitario: Decimal
    tempo_estimado_minutos: int
    tempo_executado_minutos: Optional[int]
    observacao: Optional[str]
    cancelado: bool


@dataclass(frozen=True, kw_only=True)
class OrdemServicoItemResponse:
    id: str
    item_estoque_id: str
    nome_item: str
    tipo_item: str
    quantidade: int
    valor_unitario: Decimal
    status: str


@dataclass(frozen=True, kw_only=True)
class ReservaItemSagaResponse:
    operation_id: str
    saga_id: str
    ordem_servico_item_id: str
    estado: str
    terminal_result: Optional[str]
    rejection_code: Optional[str]
    reservation_id: Optional[str]
    status_url: str


@dataclass(frozen=True, kw_only=True)
class OrdemServicoResumoResponse:
    """Resposta resumida — usada na listagem."""

    id: str
    cliente_id: str
    veiculo_id: str
    status: str
    queixa_inicial: str
    diagnostico: Optional[str]
    criado_em: datetime
    atualizado_em: datetime
    iniciado_diagnostico_em: Optional[datetime]
    diagnostico_concluido_em: Optional[datetime]


@dataclass(frozen=True, kw_only=True)
class OrdemServicoDetalheResponse:
    """Resposta completa — usada no detalhamento."""

    id: str
    cliente_id: str
    veiculo_id: str
    status: str
    queixa_inicial: str
    diagnostico: Optional[str]
    criado_em: datetime
    atualizado_em: datetime
    iniciado_diagnostico_em: Optional[datetime]
    diagnostico_concluido_em: Optional[datetime]
    payment_id: Optional[str] = None
    payment_status: Optional[str] = None
    payment_amount: Optional[Decimal] = None
    payment_confirmed_at: Optional[datetime] = None
    payment_quote_version: Optional[int] = None
    servicos: list[OrdemServicoServicoResponse] = field(default_factory=list)
    itens: list[OrdemServicoItemResponse] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Métricas — Tempo de execução por serviço do catálogo
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EstatisticaTempoExecucaoServicoResponse:
    """Estatística agregada de tempo executado para um serviço do catálogo."""

    servico_id: str
    nome_servico: str
    tempo_estimado_minutos: Optional[int]
    quantidade_ordens_servico: int
    tempo_medio_minutos: Optional[float]
    menor_tempo_minutos: Optional[int]
    maior_tempo_minutos: Optional[int]


@dataclass(frozen=True, kw_only=True)
class ExecucaoServicoResponse:
    """Dados de uma execução individual de serviço em OS finalizada/entregue."""

    ordem_servico_id: str
    ordem_servico_servico_id: str
    tempo_executado_minutos: int
    status_os: str


@dataclass(frozen=True, kw_only=True)
class ListagemExecucoesServicoResponse:
    """Listagem de execuções de um serviço do catálogo em OS finalizadas/entregues."""

    servico_id: str
    nome_servico: str
    tempo_estimado_minutos: Optional[int]
    quantidade_ordens_servico: int
    execucoes: list[ExecucaoServicoResponse] = field(default_factory=list)
