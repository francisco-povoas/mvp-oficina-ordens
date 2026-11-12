"""Testes unitários para os use cases do módulo ordens_servico."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.integrations.resources import (
    ItemEstoqueNaoEncontradoError,
    LiberarReservaResult,
    RecursoSnapshot,
    ResourceDependencyUnavailable,
    ServicoNaoEncontradoError,
)
from app.integrations.resources import (
    ItemType as TipoItemEstoque,
)
from app.integrations.resources import (
    ServicoSnapshot as Servico,
)
from app.modules.clientes.domain.exceptions import ClienteNaoEncontradoError
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    AdicionarItemNaOSRequest,
    AdicionarServicoNaOSRequest,
    CriarOrdemServicoRequest,
    RegistrarDiagnosticoRequest,
)
from app.modules.ordens_servico.application.use_cases.adicionar_item_na_ordem_servico import (
    AdicionarItemNaOrdemServicoUseCase,
    _request_hash,
)
from app.modules.ordens_servico.application.use_cases.adicionar_servico_na_ordem_servico import (
    AdicionarServicoNaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.concluir_diagnostico import (
    ConcluirDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.criar_ordem_servico import (
    CriarOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.iniciar_diagnostico import (
    IniciarDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.listar_ordens_servico import (
    ListarOrdensServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.obter_ordem_servico_por_id import (
    ObterOrdemServicoPorIdUseCase,
)
from app.modules.ordens_servico.application.use_cases.registrar_diagnostico import (
    RegistrarDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.remover_item_da_ordem_servico import (
    RemoverItemDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.remover_servico_da_ordem_servico import (
    RemoverServicoDaOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_servico import (
    OrdemServicoServico,
)
from app.modules.ordens_servico.domain.entities.reserva_item_saga import (
    EstadoReservaItemSaga,
    ReservaItemSaga,
)
from app.modules.ordens_servico.domain.exceptions import (
    ItemJaAdicionadoNaOrdemServicoError,
    OrdemServicoInvalidaError,
    OrdemServicoItemNaoEncontradoError,
    OrdemServicoNaoEncontradaError,
    OrdemServicoServicoNaoEncontradoError,
    OrdemServicoTransicaoInvalidaError,
    ReservaItemSagaIdempotenciaConflitanteError,
    ServicoJaAdicionadoNaOrdemServicoError,
)
from app.modules.ordens_servico.domain.filters.ordem_servico import (
    ListarOrdensServicoFiltro,
)
from app.modules.veiculos.domain.exceptions import VeiculoNaoEncontradoError
from app.shared.value_objects.id import ID

# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------

def _agora() -> datetime:
    return datetime.now(UTC)


def _os_fake(
    status: StatusOrdemServico = StatusOrdemServico.RECEBIDA,
    queixa_inicial: str = "Barulho no motor",
    diagnostico: str | None = None,
) -> OrdemServico:
    agora = _agora()
    return OrdemServico(
        id=ID.generate(),
        cliente_id=uuid4(),
        veiculo_id=uuid4(),
        status=status,
        queixa_inicial=queixa_inicial,
        diagnostico=diagnostico,
        criado_em=agora,
        atualizado_em=agora,
        iniciado_diagnostico_em=None,
        diagnostico_concluido_em=None,
    )


def _recurso_fake() -> RecursoSnapshot:
    return RecursoSnapshot(
        id=ID.generate(),
        tipo=TipoItemEstoque.PECA,
        nome="Filtro de óleo",
        descricao="Filtro para motor",
        valor_unitario=Decimal("35.00"),
    )


def _servico_fake() -> Servico:
    agora = _agora()
    return Servico(
        id=ID.generate(),
        nome="Troca de óleo",
        descricao="Substituição completa do óleo",
        valor_base=Decimal("120.00"),
        tempo_medio_minutos=60,
        ativo=True,
        criado_em=agora,
        atualizado_em=agora,
    )


def _os_servico_fake(
    os_id: str | None = None,
    cancelado: bool = False,
) -> OrdemServicoServico:
    return OrdemServicoServico(
        id=ID.generate(),
        ordem_servico_id=uuid4() if os_id is None else __import__("uuid").UUID(os_id),
        servico_id=uuid4(),
        nome_servico="Troca de óleo",
        descricao_servico=None,
        valor_unitario=Decimal("120.00"),
        tempo_estimado_minutos=60,
        tempo_executado_minutos=None,
        observacao=None,
        cancelado=cancelado,
    )


def _os_item_fake(
    os_id: str | None = None,
    status: StatusItemNaOS = StatusItemNaOS.RESERVADO,
    quantidade: int = 2,
) -> OrdemServicoItem:
    return OrdemServicoItem(
        id=ID.generate(),
        ordem_servico_id=uuid4() if os_id is None else __import__("uuid").UUID(os_id),
        item_estoque_id=uuid4(),
        nome_item="Filtro de óleo",
        tipo_item="PECA",
        quantidade=quantidade,
        valor_unitario=Decimal("35.00"),
        status=status,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_os_repo():
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=None)
    repo.salvar = AsyncMock()
    repo.atualizar = AsyncMock()
    repo.listar = AsyncMock(return_value=[])
    repo.salvar_servico = AsyncMock()
    repo.obter_servico_por_id = AsyncMock(return_value=None)
    repo.listar_servicos_da_os = AsyncMock(return_value=[])
    repo.atualizar_servico = AsyncMock()
    repo.obter_servico_ativo_por_servico_id = AsyncMock(return_value=None)
    repo.salvar_item = AsyncMock()
    repo.obter_item_por_id = AsyncMock(return_value=None)
    repo.listar_itens_da_os = AsyncMock(return_value=[])
    repo.atualizar_item = AsyncMock()
    repo.obter_item_ativo_por_item_estoque_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_uow(mock_os_repo):
    uow = MagicMock()
    uow.ordem_servico_repo = mock_os_repo
    uow.reserva_item_saga_repo = MagicMock()
    uow.reserva_item_saga_repo.salvar = AsyncMock()
    uow.reserva_item_saga_repo.obter_por_chave_idempotencia = AsyncMock(return_value=None)
    uow.reserva_item_saga_repo.listar_nao_terminais_da_os = AsyncMock(return_value=[])
    uow.outbox = MagicMock()
    uow.outbox.add = AsyncMock()
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    return uow


@pytest.fixture
def mock_recursos():
    recursos = MagicMock()
    recursos.reservar_item = AsyncMock()
    recursos.liberar_reserva = AsyncMock()
    recursos.receber_item_pendente = AsyncMock()
    recursos.consumir_reserva = AsyncMock()
    recursos.obter_recurso_por_id = AsyncMock()
    return recursos


@pytest.fixture
def mock_cliente_repo():
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_veiculo_repo():
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_servico_repo():
    repo = MagicMock()
    repo.obter_por_id = AsyncMock(return_value=None)
    return repo


# ---------------------------------------------------------------------------
# CriarOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestCriarOrdemServicoUseCase:
    async def test_cria_os_com_sucesso(self, mock_uow, mock_cliente_repo, mock_veiculo_repo):
        cliente = MagicMock()
        cliente_id = str(uuid4())
        veiculo_id = str(uuid4())
        cliente.id = ID.from_string(cliente_id)

        veiculo = MagicMock()
        veiculo.id = ID.from_string(veiculo_id)
        veiculo.cliente_id = __import__("uuid").UUID(cliente_id)

        mock_cliente_repo.obter_por_id = AsyncMock(return_value=cliente)
        mock_veiculo_repo.obter_por_id = AsyncMock(return_value=veiculo)

        uc = CriarOrdemServicoUseCase(
            uow=mock_uow,
            cliente_repo=mock_cliente_repo,
            veiculo_repo=mock_veiculo_repo,
        )
        dto = CriarOrdemServicoRequest(
            cliente_id=cliente_id,
            veiculo_id=veiculo_id,
            queixa_inicial="Freio falhando",
        )
        resultado = await uc.execute(dto)

        assert resultado.status == StatusOrdemServico.RECEBIDA.value
        assert resultado.queixa_inicial == "Freio falhando"
        mock_uow.ordem_servico_repo.salvar.assert_called_once()

    async def test_cliente_nao_encontrado_levanta_erro(self, mock_uow, mock_cliente_repo, mock_veiculo_repo):
        uc = CriarOrdemServicoUseCase(
            uow=mock_uow,
            cliente_repo=mock_cliente_repo,
            veiculo_repo=mock_veiculo_repo,
        )
        dto = CriarOrdemServicoRequest(
            cliente_id=str(uuid4()),
            veiculo_id=str(uuid4()),
            queixa_inicial="Problema no motor",
        )
        with pytest.raises(ClienteNaoEncontradoError):
            await uc.execute(dto)

    async def test_veiculo_nao_encontrado_levanta_erro(self, mock_uow, mock_cliente_repo, mock_veiculo_repo):
        mock_cliente_repo.obter_por_id = AsyncMock(return_value=MagicMock())
        uc = CriarOrdemServicoUseCase(
            uow=mock_uow,
            cliente_repo=mock_cliente_repo,
            veiculo_repo=mock_veiculo_repo,
        )
        dto = CriarOrdemServicoRequest(
            cliente_id=str(uuid4()),
            veiculo_id=str(uuid4()),
            queixa_inicial="Problema",
        )
        with pytest.raises(VeiculoNaoEncontradoError):
            await uc.execute(dto)

    async def test_veiculo_nao_pertence_ao_cliente_levanta_erro(
        self, mock_uow, mock_cliente_repo, mock_veiculo_repo
    ):
        cliente_id = str(uuid4())
        veiculo = MagicMock()
        # veiculo.cliente_id aponta para outro cliente
        veiculo.cliente_id = uuid4()

        mock_cliente_repo.obter_por_id = AsyncMock(return_value=MagicMock())
        mock_veiculo_repo.obter_por_id = AsyncMock(return_value=veiculo)

        uc = CriarOrdemServicoUseCase(
            uow=mock_uow,
            cliente_repo=mock_cliente_repo,
            veiculo_repo=mock_veiculo_repo,
        )
        dto = CriarOrdemServicoRequest(
            cliente_id=cliente_id,
            veiculo_id=str(uuid4()),
            queixa_inicial="Problema",
        )
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(dto)


# ---------------------------------------------------------------------------
# ListarOrdensServicoUseCase
# ---------------------------------------------------------------------------

class TestListarOrdensServicoUseCase:
    async def test_retorna_lista_vazia(self, mock_os_repo):
        uc = ListarOrdensServicoUseCase(ordem_servico_repo=mock_os_repo)
        resultado = await uc.execute(ListarOrdensServicoFiltro())
        assert resultado == []
        mock_os_repo.listar.assert_called_once()

    async def test_delega_filtros_para_o_repositorio(self, mock_os_repo):
        filtros = ListarOrdensServicoFiltro(status=StatusOrdemServico.FINALIZADA)

        uc = ListarOrdensServicoUseCase(ordem_servico_repo=mock_os_repo)
        await uc.execute(filtros)

        mock_os_repo.listar.assert_called_once_with(filtros)

    async def test_retorna_os_encontradas(self, mock_os_repo):
        os1 = _os_fake()
        os2 = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_os_repo.listar = AsyncMock(return_value=[os1, os2])

        uc = ListarOrdensServicoUseCase(ordem_servico_repo=mock_os_repo)
        resultado = await uc.execute(ListarOrdensServicoFiltro())

        assert len(resultado) == 2
        assert resultado[0].id == str(os1.id)


# ---------------------------------------------------------------------------
# ObterOrdemServicoPorIdUseCase
# ---------------------------------------------------------------------------

class TestObterOrdemServicoPorIdUseCase:
    async def test_retorna_detalhe_com_servicos_e_itens(self, mock_os_repo):
        os = _os_fake()
        oss = _os_servico_fake()
        osi = _os_item_fake()
        mock_os_repo.obter_por_id = AsyncMock(return_value=os)
        mock_os_repo.listar_servicos_da_os = AsyncMock(return_value=[oss])
        mock_os_repo.listar_itens_da_os = AsyncMock(return_value=[osi])

        uc = ObterOrdemServicoPorIdUseCase(ordem_servico_repo=mock_os_repo)
        resultado = await uc.execute(str(os.id))

        assert resultado.id == str(os.id)
        assert len(resultado.servicos) == 1
        assert len(resultado.itens) == 1

    async def test_os_nao_encontrada_levanta_erro(self, mock_os_repo):
        uc = ObterOrdemServicoPorIdUseCase(ordem_servico_repo=mock_os_repo)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()))


# ---------------------------------------------------------------------------
# IniciarDiagnosticoOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestIniciarDiagnosticoUseCase:
    async def test_inicia_diagnostico_com_sucesso(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = IniciarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        resultado = await uc.execute(str(os.id))

        assert resultado.status == StatusOrdemServico.EM_DIAGNOSTICO.value
        mock_uow.ordem_servico_repo.atualizar.assert_called_once()
        assert mock_uow.ordem_servico_repo.atualizar.call_args.args[0].version == 2

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow):
        uc = IniciarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()))

    async def test_transicao_invalida_levanta_erro(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = IniciarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(str(os.id))


# ---------------------------------------------------------------------------
# RegistrarDiagnosticoOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestRegistrarDiagnosticoUseCase:
    async def test_registra_diagnostico_com_sucesso(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RegistrarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        dto = RegistrarDiagnosticoRequest(diagnostico="Desgaste nas pastilhas")
        resultado = await uc.execute(str(os.id), dto)

        assert resultado.diagnostico == "Desgaste nas pastilhas"
        mock_uow.ordem_servico_repo.atualizar.assert_called_once()
        assert mock_uow.ordem_servico_repo.atualizar.call_args.args[0].version == 2

    async def test_diagnostico_vazio_levanta_erro(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RegistrarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        dto = RegistrarDiagnosticoRequest(diagnostico="")
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id), dto)

    async def test_diagnostico_somente_espacos_levanta_erro(self, mock_uow):
        uc = RegistrarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        dto = RegistrarDiagnosticoRequest(diagnostico="   ")
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(uuid4()), dto)

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow):
        uc = RegistrarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        dto = RegistrarDiagnosticoRequest(diagnostico="Diagnóstico")
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()), dto)

    async def test_status_invalido_levanta_erro(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RegistrarDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        dto = RegistrarDiagnosticoRequest(diagnostico="Diagnóstico válido")
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(str(os.id), dto)


# ---------------------------------------------------------------------------
# AdicionarServicoNaOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestAdicionarServicoNaOrdemServicoUseCase:
    async def test_adiciona_servico_com_sucesso(self, mock_uow, mock_servico_repo):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        servico = _servico_fake()
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_servico_repo.obter_por_id = AsyncMock(return_value=servico)

        uc = AdicionarServicoNaOrdemServicoUseCase(uow=mock_uow, servico_repo=mock_servico_repo)
        dto = AdicionarServicoNaOSRequest(servico_id=str(servico.id), observacao=None)
        resultado = await uc.execute(str(os.id), dto)

        assert resultado.nome_servico == servico.nome
        assert resultado.cancelado is False
        mock_uow.ordem_servico_repo.salvar_servico.assert_called_once()
        assert mock_uow.ordem_servico_repo.atualizar.call_args.args[0].version == 2

    async def test_servico_nao_encontrado_levanta_erro(self, mock_uow, mock_servico_repo):
        uc = AdicionarServicoNaOrdemServicoUseCase(uow=mock_uow, servico_repo=mock_servico_repo)
        dto = AdicionarServicoNaOSRequest(servico_id=str(uuid4()), observacao=None)
        with pytest.raises(ServicoNaoEncontradoError):
            await uc.execute(str(uuid4()), dto)

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow, mock_servico_repo):
        mock_servico_repo.obter_por_id = AsyncMock(return_value=_servico_fake())
        uc = AdicionarServicoNaOrdemServicoUseCase(uow=mock_uow, servico_repo=mock_servico_repo)
        dto = AdicionarServicoNaOSRequest(servico_id=str(uuid4()), observacao=None)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()), dto)

    async def test_os_status_errado_levanta_erro(self, mock_uow, mock_servico_repo):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_servico_repo.obter_por_id = AsyncMock(return_value=_servico_fake())

        uc = AdicionarServicoNaOrdemServicoUseCase(uow=mock_uow, servico_repo=mock_servico_repo)
        dto = AdicionarServicoNaOSRequest(servico_id=str(uuid4()), observacao=None)
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(str(os.id), dto)

    async def test_servico_duplicado_levanta_erro(self, mock_uow, mock_servico_repo):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        servico = _servico_fake()
        existente = _os_servico_fake()

        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_servico_ativo_por_servico_id = AsyncMock(
            return_value=existente
        )
        mock_servico_repo.obter_por_id = AsyncMock(return_value=servico)

        uc = AdicionarServicoNaOrdemServicoUseCase(uow=mock_uow, servico_repo=mock_servico_repo)
        dto = AdicionarServicoNaOSRequest(servico_id=str(servico.id), observacao=None)
        with pytest.raises(ServicoJaAdicionadoNaOrdemServicoError):
            await uc.execute(str(os.id), dto)


# ---------------------------------------------------------------------------
# RemoverServicoDaOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestRemoverServicoDaOrdemServicoUseCase:
    async def test_remove_servico_com_sucesso(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        # Garante que o os_servico.ordem_servico_id == os.id
        import uuid as _uuid
        oss = OrdemServicoServico(
            id=ID.generate(),
            ordem_servico_id=_uuid.UUID(str(os.id)),
            servico_id=uuid4(),
            nome_servico="Troca de óleo",
            descricao_servico=None,
            valor_unitario=Decimal("120.00"),
            tempo_estimado_minutos=60,
            tempo_executado_minutos=None,
            observacao=None,
            cancelado=False,
        )
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_servico_por_id = AsyncMock(return_value=oss)

        uc = RemoverServicoDaOrdemServicoUseCase(uow=mock_uow)
        await uc.execute(str(os.id), str(oss.id))

        mock_uow.ordem_servico_repo.atualizar_servico.assert_called_once()
        # Verifica que foi marcado como cancelado
        args = mock_uow.ordem_servico_repo.atualizar_servico.call_args[0][0]
        assert args.cancelado is True
        assert mock_uow.ordem_servico_repo.atualizar.call_args.args[0].version == 2

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow):
        uc = RemoverServicoDaOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()), str(uuid4()))

    async def test_os_status_errado_levanta_erro(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RemoverServicoDaOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(str(os.id), str(uuid4()))

    async def test_servico_nao_encontrado_levanta_erro(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RemoverServicoDaOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoServicoNaoEncontradoError):
            await uc.execute(str(os.id), str(uuid4()))

    async def test_servico_ja_cancelado_levanta_erro(self, mock_uow):
        import uuid as _uuid
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        oss = OrdemServicoServico(
            id=ID.generate(),
            ordem_servico_id=_uuid.UUID(str(os.id)),
            servico_id=uuid4(),
            nome_servico="Troca de óleo",
            descricao_servico=None,
            valor_unitario=Decimal("120.00"),
            tempo_estimado_minutos=60,
            tempo_executado_minutos=None,
            observacao=None,
            cancelado=True,  # já cancelado
        )
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_servico_por_id = AsyncMock(return_value=oss)

        uc = RemoverServicoDaOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id), str(oss.id))


# ---------------------------------------------------------------------------
# AdicionarItemNaOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestAdicionarItemNaOrdemServicoUseCase:
    async def test_inicia_saga_e_outbox_sem_criar_item(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        recurso = _recurso_fake()
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_recursos.obter_recurso_por_id.return_value = recurso

        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        dto = AdicionarItemNaOSRequest(item_estoque_id=str(recurso.id), quantidade=3)
        correlacao = uuid4()
        resultado = await uc.execute(
            str(os.id),
            dto,
            idempotency_key="request-123",
            correlation_id=correlacao,
        )

        assert resultado.estado == EstadoReservaItemSaga.PENDING.value
        assert resultado.operation_id
        mock_recursos.obter_recurso_por_id.assert_awaited_once_with(recurso.id)
        saga = mock_uow.reserva_item_saga_repo.salvar.await_args.args[0]
        assert str(saga.operation_id) == resultado.operation_id
        assert saga.resource_id == recurso.id.value
        mensagem = mock_uow.outbox.add.await_args.args[0]
        assert mensagem.aggregate_id == saga.operation_id
        assert mensagem.saga_id == saga.id
        assert mensagem.event_type == "ReserveItem.v1"
        assert mensagem.correlation_id == correlacao
        assert mensagem.payload["resource_snapshot"]["name"] == recurso.nome
        mock_uow.ordem_servico_repo.salvar_item.assert_not_awaited()
        ordem_atualizada = mock_uow.ordem_servico_repo.atualizar.await_args.args[0]
        assert ordem_atualizada.version == os.version + 1

    async def test_requisicao_repetida_devolve_mesma_operacao(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        recurso = _recurso_fake()
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        existente = ReservaItemSaga.iniciar(
            saga_id=uuid4(),
            operation_id=uuid4(),
            ordem_servico_id=os.id.value,
            ordem_servico_item_id=uuid4(),
            resource_id=recurso.id.value,
            quantity=3,
            correlation_id=uuid4(),
            idempotency_key="request-123",
            request_hash=_request_hash(
                ordem_servico_id=os.id.value,
                resource_id=recurso.id.value,
                quantity=3,
            ),
            reserve_message_id=uuid4(),
        )
        mock_uow.reserva_item_saga_repo.obter_por_chave_idempotencia.return_value = existente

        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        dto = AdicionarItemNaOSRequest(item_estoque_id=str(recurso.id), quantidade=3)
        resultado = await uc.execute(
            str(os.id),
            dto,
            idempotency_key="request-123",
            correlation_id=uuid4(),
        )

        assert resultado.operation_id == str(existente.operation_id)
        mock_recursos.obter_recurso_por_id.assert_not_awaited()
        mock_uow.outbox.add.assert_not_awaited()
        mock_uow.ordem_servico_repo.atualizar.assert_not_awaited()

    async def test_mesma_chave_com_payload_divergente_gera_conflito(
        self, mock_uow, mock_recursos
    ):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        recurso = _recurso_fake()
        mock_uow.ordem_servico_repo.obter_por_id.return_value = os
        mock_uow.reserva_item_saga_repo.obter_por_chave_idempotencia.return_value = (
            ReservaItemSaga.iniciar(
                saga_id=uuid4(),
                operation_id=uuid4(),
                ordem_servico_id=os.id.value,
                ordem_servico_item_id=uuid4(),
                resource_id=recurso.id.value,
                quantity=1,
                correlation_id=uuid4(),
                idempotency_key="request-123",
                request_hash="a" * 64,
                reserve_message_id=uuid4(),
            )
        )

        with pytest.raises(ReservaItemSagaIdempotenciaConflitanteError):
            await AdicionarItemNaOrdemServicoUseCase(
                uow=mock_uow, recursos=mock_recursos
            ).execute(
                str(os.id),
                AdicionarItemNaOSRequest(item_estoque_id=str(recurso.id), quantidade=2),
                idempotency_key="request-123",
                correlation_id=uuid4(),
            )

        mock_uow.outbox.add.assert_not_awaited()

    async def test_quantidade_zero_levanta_erro(self, mock_uow, mock_recursos):
        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        dto = AdicionarItemNaOSRequest(item_estoque_id=str(uuid4()), quantidade=0)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(
                str(uuid4()),
                dto,
                idempotency_key="request-123",
                correlation_id=uuid4(),
            )
        mock_recursos.obter_recurso_por_id.assert_not_awaited()

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow, mock_recursos):
        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        dto = AdicionarItemNaOSRequest(item_estoque_id=str(uuid4()), quantidade=1)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(
                str(uuid4()),
                dto,
                idempotency_key="request-123",
                correlation_id=uuid4(),
            )

    async def test_os_status_errado_levanta_erro(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        dto = AdicionarItemNaOSRequest(item_estoque_id=str(uuid4()), quantidade=1)
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(
                str(os.id),
                dto,
                idempotency_key="request-123",
                correlation_id=uuid4(),
            )

    async def test_item_duplicado_levanta_erro(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        existente = _os_item_fake()
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_item_ativo_por_item_estoque_id = AsyncMock(
            return_value=existente
        )

        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        dto = AdicionarItemNaOSRequest(item_estoque_id=str(uuid4()), quantidade=1)
        with pytest.raises(ItemJaAdicionadoNaOrdemServicoError):
            await uc.execute(
                str(os.id),
                dto,
                idempotency_key="request-123",
                correlation_id=uuid4(),
            )

    async def test_recurso_ausente_nao_cria_saga(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_uow.ordem_servico_repo.obter_por_id.return_value = os
        mock_recursos.obter_recurso_por_id.return_value = None
        uc = AdicionarItemNaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)

        with pytest.raises(ItemEstoqueNaoEncontradoError):
            await uc.execute(
                str(os.id),
                AdicionarItemNaOSRequest(item_estoque_id=str(uuid4()), quantidade=1),
                idempotency_key="request-123",
                correlation_id=uuid4(),
            )

        mock_uow.reserva_item_saga_repo.salvar.assert_not_awaited()
        mock_uow.outbox.add.assert_not_awaited()
        mock_uow.ordem_servico_repo.atualizar.assert_not_awaited()


# ---------------------------------------------------------------------------
# RemoverItemDaOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestRemoverItemDaOrdemServicoUseCase:
    async def test_remove_reservado_somente_apos_resultado_de_liberacao(
        self, mock_uow, mock_recursos
    ):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        osi = _os_item_fake(str(os.id), status=StatusItemNaOS.RESERVADO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_item_por_id = AsyncMock(return_value=osi)
        mock_recursos.liberar_reserva.side_effect = lambda command: LiberarReservaResult(
            operation_id=command.operation_id,
            recurso_id=command.recurso_id,
            quantidade=command.quantidade,
            reservation_id=uuid4(),
            liberado_em=_agora(),
        )

        uc = RemoverItemDaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        await uc.execute(str(os.id), str(osi.id))

        mock_recursos.liberar_reserva.assert_awaited_once()
        mock_uow.ordem_servico_repo.atualizar_item.assert_called_once()
        osi_cancelado = mock_uow.ordem_servico_repo.atualizar_item.call_args[0][0]
        assert osi_cancelado.status == StatusItemNaOS.CANCELADO

    async def test_remove_item_a_receber_sem_operacao_fisica(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        osi = _os_item_fake(str(os.id), status=StatusItemNaOS.A_RECEBER)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_item_por_id = AsyncMock(return_value=osi)

        uc = RemoverItemDaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        await uc.execute(str(os.id), str(osi.id))

        mock_recursos.liberar_reserva.assert_not_awaited()
        mock_uow.ordem_servico_repo.atualizar_item.assert_called_once()

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow, mock_recursos):
        uc = RemoverItemDaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()), str(uuid4()))

    async def test_os_status_errado_levanta_erro(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RemoverItemDaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(str(os.id), str(uuid4()))

    async def test_item_nao_encontrado_levanta_erro(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = RemoverItemDaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        with pytest.raises(OrdemServicoItemNaoEncontradoError):
            await uc.execute(str(os.id), str(uuid4()))

    async def test_item_ja_cancelado_levanta_erro(self, mock_uow, mock_recursos):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        osi = _os_item_fake(str(os.id), status=StatusItemNaOS.CANCELADO)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.obter_item_por_id = AsyncMock(return_value=osi)

        uc = RemoverItemDaOrdemServicoUseCase(uow=mock_uow, recursos=mock_recursos)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id), str(osi.id))

    async def test_indisponibilidade_nao_cancela_reserva_local(
        self, mock_uow, mock_recursos
    ):
        os = _os_fake(status=StatusOrdemServico.EM_DIAGNOSTICO)
        osi = _os_item_fake(str(os.id), status=StatusItemNaOS.RESERVADO)
        mock_uow.ordem_servico_repo.obter_por_id.return_value = os
        mock_uow.ordem_servico_repo.obter_item_por_id.return_value = osi
        mock_recursos.liberar_reserva.side_effect = ResourceDependencyUnavailable("Etapa 2")

        with pytest.raises(ResourceDependencyUnavailable):
            await RemoverItemDaOrdemServicoUseCase(
                uow=mock_uow, recursos=mock_recursos
            ).execute(str(os.id), str(osi.id))

        mock_uow.ordem_servico_repo.atualizar_item.assert_not_awaited()
        mock_uow.ordem_servico_repo.atualizar.assert_not_awaited()


# ---------------------------------------------------------------------------
# ConcluirDiagnosticoOrdemServicoUseCase
# ---------------------------------------------------------------------------

class TestConcluirDiagnosticoUseCase:
    async def test_conclui_diagnostico_com_sucesso(self, mock_uow):
        os = _os_fake(
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            diagnostico="Pastilhas gastas",
        )
        oss_ativo = _os_servico_fake(cancelado=False)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.listar_servicos_da_os = AsyncMock(
            return_value=[oss_ativo]
        )

        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        resultado = await uc.execute(str(os.id))

        assert resultado.status == StatusOrdemServico.DIAGNOSTICO_CONCLUIDO.value
        assert resultado.diagnostico_concluido_em is not None
        mock_uow.ordem_servico_repo.atualizar.assert_called_once()
        assert mock_uow.ordem_servico_repo.atualizar.call_args.args[0].version == 2

    async def test_saga_pendente_impede_conclusao(self, mock_uow):
        os = _os_fake(
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            diagnostico="Pastilhas gastas",
        )
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.reserva_item_saga_repo.listar_nao_terminais_da_os = AsyncMock(
            return_value=[MagicMock()]
        )

        with pytest.raises(OrdemServicoInvalidaError, match="reserva de item pendente"):
            await ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow).execute(
                str(os.id)
            )

        mock_uow.ordem_servico_repo.atualizar.assert_not_awaited()

    async def test_os_nao_encontrada_levanta_erro(self, mock_uow):
        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoNaoEncontradaError):
            await uc.execute(str(uuid4()))

    async def test_status_invalido_levanta_erro(self, mock_uow):
        os = _os_fake(status=StatusOrdemServico.RECEBIDA)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoTransicaoInvalidaError):
            await uc.execute(str(os.id))

    async def test_sem_diagnostico_levanta_erro(self, mock_uow):
        os = _os_fake(
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            diagnostico=None,  # sem diagnóstico
        )
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id))

    async def test_diagnostico_em_branco_levanta_erro(self, mock_uow):
        os = _os_fake(
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            diagnostico="   ",
        )
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)

        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id))

    async def test_sem_servicos_ativos_levanta_erro(self, mock_uow):
        os = _os_fake(
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            diagnostico="Diagnóstico completo",
        )
        oss_cancelado = _os_servico_fake(cancelado=True)
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.listar_servicos_da_os = AsyncMock(
            return_value=[oss_cancelado]
        )

        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id))

    async def test_lista_vazia_de_servicos_levanta_erro(self, mock_uow):
        os = _os_fake(
            status=StatusOrdemServico.EM_DIAGNOSTICO,
            diagnostico="Diagnóstico completo",
        )
        mock_uow.ordem_servico_repo.obter_por_id = AsyncMock(return_value=os)
        mock_uow.ordem_servico_repo.listar_servicos_da_os = AsyncMock(return_value=[])

        uc = ConcluirDiagnosticoOrdemServicoUseCase(uow=mock_uow)
        with pytest.raises(OrdemServicoInvalidaError):
            await uc.execute(str(os.id))
