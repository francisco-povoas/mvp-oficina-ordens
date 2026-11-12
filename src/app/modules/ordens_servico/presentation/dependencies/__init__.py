"""Dependências de infraestrutura do módulo Ordens de Serviço para injeção via FastAPI."""

from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.integrations.finance import FinanceHTTPClient, FinanceStatusPort
from app.integrations.resources import (
    RecursoCatalogoPort,
    RecursosHTTPAdapter,
    RecursosIndisponiveisAdapter,
    RecursosOperacoesPort,
    ServicoCatalogoPort,
)
from app.modules.clientes.infrastructure.db.repositories.cliente_repo import ClienteRepo
from app.modules.ordens_servico.application.use_cases import (
    AdicionarItemNaOrdemServicoUseCase,
    AdicionarServicoNaOrdemServicoUseCase,
    CancelarReservaItemSagaUseCase,
    ConcluirDiagnosticoOrdemServicoUseCase,
    ConfirmarRecebimentoItemDaOrdemServicoUseCase,
    CriarOrdemServicoUseCase,
    EntregarOrdemServicoUseCase,
    FinalizarOrdemServicoUseCase,
    IniciarDiagnosticoOrdemServicoUseCase,
    IniciarExecucaoOrdemServicoUseCase,
    ListarExecucoesServicoUseCase,
    ListarOrdensServicoUseCase,
    ObterEstatisticaTempoExecucaoServicoUseCase,
    ObterOrdemServicoPorIdUseCase,
    ObterReservaItemSagaUseCase,
    RegistrarDiagnosticoOrdemServicoUseCase,
    RegistrarTempoExecutadoServicoUseCase,
    RemoverItemDaOrdemServicoUseCase,
    RemoverServicoDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_repo import (
    OrdemServicoRepo as OrdemServicoRepoProtocol,
)
from app.modules.ordens_servico.domain.ports.ordem_servico_uow import (
    OrdemServicoUnitOfWork as OrdemServicoUoWProtocol,
)
from app.modules.ordens_servico.domain.ports.reserva_item_saga_repo import (
    ReservaItemSagaRepo as ReservaItemSagaRepoProtocol,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.modules.ordens_servico.infrastructure.db.repositories.reserva_item_saga_repo import (
    ReservaItemSagaRepo,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    ordem_servico_uow_factory,
)
from app.modules.veiculos.infrastructure.db.repositories.veiculo_repo import VeiculoRepo
from app.shared.infra.db import DBSession, get_db_session

# ---------------------------------------------------------------------------
# Infraestrutura
# ---------------------------------------------------------------------------


def get_ordem_servico_repo(
    session: Annotated[DBSession, Depends(get_db_session)],
) -> OrdemServicoRepoProtocol:
    return OrdemServicoRepo(session)  # type: ignore[return-value]


def get_recursos_adapter(request: Request) -> RecursosIndisponiveisAdapter:
    settings = request.app.state.settings
    return RecursosHTTPAdapter(
        base_url=settings.resources_base_url,
        service_name="mvp-oficina-recursos",
        token_provider=request.app.state.service_token_provider,
        correlation_id=str(request.state.correlation_id),
        timeout=httpx.Timeout(
            connect=settings.internal_connect_timeout_seconds,
            read=settings.internal_read_timeout_seconds,
            write=settings.internal_read_timeout_seconds,
            pool=settings.internal_connect_timeout_seconds,
        ),
    )


_RecursosOperacoesDep = Annotated[RecursosOperacoesPort, Depends(get_recursos_adapter)]
_RecursoCatalogoDep = Annotated[RecursoCatalogoPort, Depends(get_recursos_adapter)]


def get_financeiro_adapter(request: Request) -> FinanceStatusPort:
    settings = request.app.state.settings
    return FinanceHTTPClient(
        base_url=settings.finance_base_url,
        service_name="mvp-oficina-financeiro",
        token_provider=request.app.state.service_token_provider,
        correlation_id=str(request.state.correlation_id),
        timeout=httpx.Timeout(
            connect=settings.internal_connect_timeout_seconds,
            read=settings.internal_read_timeout_seconds,
            write=settings.internal_read_timeout_seconds,
            pool=settings.internal_connect_timeout_seconds,
        ),
    )


_FinanceiroDep = Annotated[FinanceStatusPort, Depends(get_financeiro_adapter)]


def get_ordem_servico_uow(
    session: Annotated[DBSession, Depends(get_db_session)],
) -> OrdemServicoUoWProtocol:
    return ordem_servico_uow_factory(session)  # type: ignore[return-value]


def get_reserva_item_saga_repo(
    session: Annotated[DBSession, Depends(get_db_session)],
) -> ReservaItemSagaRepoProtocol:
    return ReservaItemSagaRepo(session)  # type: ignore[return-value]


def get_cliente_repo(
    session: Annotated[DBSession, Depends(get_db_session)],
) -> ClienteRepo:
    return ClienteRepo(session)


def get_veiculo_repo(
    session: Annotated[DBSession, Depends(get_db_session)],
) -> VeiculoRepo:
    return VeiculoRepo(session)


def get_servico_repo(request: Request) -> ServicoCatalogoPort:
    return get_recursos_adapter(request)


_RepoDep = Annotated[OrdemServicoRepoProtocol, Depends(get_ordem_servico_repo)]
_SagaRepoDep = Annotated[ReservaItemSagaRepoProtocol, Depends(get_reserva_item_saga_repo)]
_UoWDep = Annotated[OrdemServicoUoWProtocol, Depends(get_ordem_servico_uow)]
_ClienteRepoDep = Annotated[ClienteRepo, Depends(get_cliente_repo)]
_VeiculoRepoDep = Annotated[VeiculoRepo, Depends(get_veiculo_repo)]
_ServicoRepoDep = Annotated[ServicoCatalogoPort, Depends(get_servico_repo)]


# ---------------------------------------------------------------------------
# Use Cases
# ---------------------------------------------------------------------------


def get_criar_ordem_servico(
    uow: _UoWDep,
    cliente_repo: _ClienteRepoDep,
    veiculo_repo: _VeiculoRepoDep,
) -> CriarOrdemServicoUseCase:
    return CriarOrdemServicoUseCase(
        uow=uow, cliente_repo=cliente_repo, veiculo_repo=veiculo_repo
    )


def get_listar_ordens_servico(repo: _RepoDep) -> ListarOrdensServicoUseCase:
    return ListarOrdensServicoUseCase(ordem_servico_repo=repo)


def get_obter_ordem_servico_por_id(repo: _RepoDep) -> ObterOrdemServicoPorIdUseCase:
    return ObterOrdemServicoPorIdUseCase(ordem_servico_repo=repo)


def get_iniciar_diagnostico(uow: _UoWDep) -> IniciarDiagnosticoOrdemServicoUseCase:
    return IniciarDiagnosticoOrdemServicoUseCase(uow=uow)


def get_registrar_diagnostico(uow: _UoWDep) -> RegistrarDiagnosticoOrdemServicoUseCase:
    return RegistrarDiagnosticoOrdemServicoUseCase(uow=uow)


def get_adicionar_servico(
    uow: _UoWDep, servico_repo: _ServicoRepoDep
) -> AdicionarServicoNaOrdemServicoUseCase:
    return AdicionarServicoNaOrdemServicoUseCase(uow=uow, servico_repo=servico_repo)


def get_remover_servico(uow: _UoWDep) -> RemoverServicoDaOrdemServicoUseCase:
    return RemoverServicoDaOrdemServicoUseCase(uow=uow)


def get_adicionar_item(
    uow: _UoWDep, recursos: _RecursoCatalogoDep
) -> AdicionarItemNaOrdemServicoUseCase:
    return AdicionarItemNaOrdemServicoUseCase(uow=uow, recursos=recursos)


def get_obter_reserva_item_saga(repo: _SagaRepoDep) -> ObterReservaItemSagaUseCase:
    return ObterReservaItemSagaUseCase(repo=repo)


def get_cancelar_reserva_item_saga(
    uow: _UoWDep,
) -> CancelarReservaItemSagaUseCase:
    return CancelarReservaItemSagaUseCase(uow=uow)


def get_remover_item(
    uow: _UoWDep, recursos: _RecursosOperacoesDep
) -> RemoverItemDaOrdemServicoUseCase:
    return RemoverItemDaOrdemServicoUseCase(uow=uow, recursos=recursos)


def get_concluir_diagnostico(uow: _UoWDep) -> ConcluirDiagnosticoOrdemServicoUseCase:
    return ConcluirDiagnosticoOrdemServicoUseCase(uow=uow)


def get_confirmar_recebimento_item(
    uow: _UoWDep, recursos: _RecursosOperacoesDep
) -> ConfirmarRecebimentoItemDaOrdemServicoUseCase:
    return ConfirmarRecebimentoItemDaOrdemServicoUseCase(uow=uow, recursos=recursos)


def get_iniciar_execucao(uow: _UoWDep) -> IniciarExecucaoOrdemServicoUseCase:
    return IniciarExecucaoOrdemServicoUseCase(uow=uow)


def get_registrar_tempo_executado(uow: _UoWDep) -> RegistrarTempoExecutadoServicoUseCase:
    return RegistrarTempoExecutadoServicoUseCase(uow=uow)


def get_finalizar_ordem_servico(uow: _UoWDep) -> FinalizarOrdemServicoUseCase:
    return FinalizarOrdemServicoUseCase(uow=uow)


def get_entregar_ordem_servico(
    uow: _UoWDep,
    recursos: _RecursosOperacoesDep,
    financeiro: _FinanceiroDep,
) -> EntregarOrdemServicoUseCase:
    return EntregarOrdemServicoUseCase(
        uow=uow,
        recursos=recursos,
        financeiro=financeiro,
    )


def get_obter_estatistica_tempo_execucao_servico(
    repo: _RepoDep,
    servico_repo: _ServicoRepoDep,
) -> ObterEstatisticaTempoExecucaoServicoUseCase:
    return ObterEstatisticaTempoExecucaoServicoUseCase(
        ordem_servico_repo=repo,
        servico_repo=servico_repo,
    )


def get_listar_execucoes_servico(
    repo: _RepoDep,
    servico_repo: _ServicoRepoDep,
) -> ListarExecucoesServicoUseCase:
    return ListarExecucoesServicoUseCase(
        ordem_servico_repo=repo,
        servico_repo=servico_repo,
    )


# ---------------------------------------------------------------------------
# Aliases Annotated para uso nos routers
# ---------------------------------------------------------------------------

CriarOrdemServicoDep = Annotated[CriarOrdemServicoUseCase, Depends(get_criar_ordem_servico)]
ListarOrdensServicoDep = Annotated[ListarOrdensServicoUseCase, Depends(get_listar_ordens_servico)]
ObterOrdemServicoPorIdDep = Annotated[ObterOrdemServicoPorIdUseCase, Depends(get_obter_ordem_servico_por_id)]
IniciarDiagnosticoDep = Annotated[IniciarDiagnosticoOrdemServicoUseCase, Depends(get_iniciar_diagnostico)]
RegistrarDiagnosticoDep = Annotated[RegistrarDiagnosticoOrdemServicoUseCase, Depends(get_registrar_diagnostico)]
AdicionarServicoDep = Annotated[AdicionarServicoNaOrdemServicoUseCase, Depends(get_adicionar_servico)]
RemoverServicoDep = Annotated[RemoverServicoDaOrdemServicoUseCase, Depends(get_remover_servico)]
AdicionarItemDep = Annotated[AdicionarItemNaOrdemServicoUseCase, Depends(get_adicionar_item)]
ObterReservaItemSagaDep = Annotated[
    ObterReservaItemSagaUseCase,
    Depends(get_obter_reserva_item_saga),
]
CancelarReservaItemSagaDep = Annotated[
    CancelarReservaItemSagaUseCase,
    Depends(get_cancelar_reserva_item_saga),
]
RemoverItemDep = Annotated[RemoverItemDaOrdemServicoUseCase, Depends(get_remover_item)]
ConcluirDiagnosticoDep = Annotated[ConcluirDiagnosticoOrdemServicoUseCase, Depends(get_concluir_diagnostico)]
ConfirmarRecebimentoItemDep = Annotated[ConfirmarRecebimentoItemDaOrdemServicoUseCase, Depends(get_confirmar_recebimento_item)]
IniciarExecucaoDep = Annotated[IniciarExecucaoOrdemServicoUseCase, Depends(get_iniciar_execucao)]
RegistrarTempoExecutadoDep = Annotated[RegistrarTempoExecutadoServicoUseCase, Depends(get_registrar_tempo_executado)]
FinalizarOrdemServicoDep = Annotated[FinalizarOrdemServicoUseCase, Depends(get_finalizar_ordem_servico)]
ObterEstatisticaTempoExecucaoDep = Annotated[ObterEstatisticaTempoExecucaoServicoUseCase, Depends(get_obter_estatistica_tempo_execucao_servico)]
ListarExecucoesServicoDep = Annotated[ListarExecucoesServicoUseCase, Depends(get_listar_execucoes_servico)]
EntregarOrdemServicoDep = Annotated[EntregarOrdemServicoUseCase, Depends(get_entregar_ordem_servico)]
