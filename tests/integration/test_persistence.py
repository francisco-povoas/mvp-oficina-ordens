from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.clientes.domain.entities.cliente import Cliente
from app.modules.clientes.domain.value_objects.cpf_cnpj import CpfCnpj
from app.modules.clientes.domain.value_objects.telefone import Telefone
from app.modules.clientes.domain.value_objects.tipo_pessoa import TipoPessoa
from app.modules.clientes.infrastructure.db.models.cliente import ClienteModel
from app.modules.clientes.infrastructure.db.repositories.cliente_repo import ClienteRepo
from app.modules.clientes.infrastructure.db.uow.cliente_uow import ClienteUnitOfWork
from app.modules.iam.domain.entities.usuario import Usuario, UsuarioPerfil
from app.modules.iam.infrastructure.db.models.perfil import PerfilModel
from app.modules.iam.infrastructure.db.repositories.usuario_repo import UsuarioRepo
from app.modules.ordens_servico.application.dtos.ordem_servico import (
    RegistrarDiagnosticoRequest,
)
from app.modules.ordens_servico.application.use_cases.iniciar_diagnostico import (
    IniciarDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.application.use_cases.registrar_diagnostico import (
    RegistrarDiagnosticoOrdemServicoUseCase,
)
from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrdemServico,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_item import (
    OrdemServicoItem,
    StatusItemNaOS,
)
from app.modules.ordens_servico.domain.entities.ordem_servico_servico import OrdemServicoServico
from app.modules.ordens_servico.domain.exceptions import (
    OrdemServicoConflitoDeVersaoError,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_item_model import (
    OrdemServicoItemModel,
)
from app.modules.ordens_servico.infrastructure.db.repositories.ordem_servico_repo import (
    OrdemServicoRepo,
)
from app.modules.ordens_servico.infrastructure.db.uow.ordem_servico_uow import (
    OrdemServicoUnitOfWork,
)
from app.modules.veiculos.domain.entities.veiculo import Veiculo
from app.modules.veiculos.domain.value_objects.placa import Placa
from app.modules.veiculos.infrastructure.db.repositories.veiculo_repo import VeiculoRepo
from app.shared.infra.db.outbox_model import OutboxMessageModel
from app.shared.value_objects.email import Email
from app.shared.value_objects.id import ID
from app.shared.value_objects.password import Password


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _cliente() -> Cliente:
    now = datetime.now(UTC)
    return Cliente(
        id=ID.generate(),
        tipo_pessoa=TipoPessoa.PF,
        nome_razao_social="Cliente PostgreSQL",
        cpf_cnpj=CpfCnpj("12345678901"),
        telefone=Telefone("71999999999"),
        email=Email("cliente@example.com"),
        cep="40000000",
        logradouro="Rua dos Testes",
        numero="16",
        complemento=None,
        bairro="Centro",
        cidade="Salvador",
        uf="BA",
        ativo=True,
        criado_em=now,
        atualizado_em=now,
    )


async def _persistir_cliente_veiculo_os(
    session: AsyncSession,
    *,
    status: StatusOrdemServico = StatusOrdemServico.RECEBIDA,
) -> tuple[Cliente, Veiculo, OrdemServico]:
    now = datetime.now(UTC)
    cliente = _cliente()
    veiculo = Veiculo(
        id=ID.generate(),
        cliente_id=cliente.id.value,
        placa=Placa("ABC1D23"),
        marca="Ford",
        modelo="Ka",
        ano_fabricacao=2020,
        ano_modelo=2021,
        cor="Prata",
        criado_em=now,
        atualizado_em=now,
    )
    ordem = OrdemServico(
        id=ID.generate(),
        cliente_id=cliente.id.value,
        veiculo_id=veiculo.id.value,
        status=status,
        queixa_inicial="Ruído no motor",
        diagnostico="Correia desgastada",
        criado_em=now,
        atualizado_em=now,
        iniciado_diagnostico_em=now,
        diagnostico_concluido_em=now,
    )
    await ClienteRepo(session).salvar(cliente)
    await session.flush()
    await VeiculoRepo(session).salvar(veiculo)
    await session.flush()
    await OrdemServicoRepo(session).salvar(ordem)
    await session.flush()
    return cliente, veiculo, ordem


async def test_persiste_e_recupera_usuario_cliente_veiculo_e_ordem(
    db_engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    usuario = Usuario(
        id=ID.generate(),
        nome="Mecânico Integração",
        email=Email("mecanico@example.com"),
        senha=Password("hash-seguro-de-teste"),
        criado_em=now,
        atualizado_em=now,
        ativo=True,
    )
    perfil_id = ID.generate()
    factory = _factory(db_engine)

    async with factory() as session:
        session.add(
            PerfilModel(
                id=perfil_id.value,
                nome="Mecanico",
                descricao="Perfil de mecânico",
                criado_em=now,
                atualizado_em=now,
                ativo=True,
            )
        )
        await session.flush()
        usuario_repo = UsuarioRepo(session)
        await usuario_repo.salvar(usuario)
        await session.flush()
        await usuario_repo.adicionar_perfil(
            UsuarioPerfil(
                id=ID.generate(),
                usuario_id=usuario.id,
                perfil_id=perfil_id,
                criado_em=now,
            )
        )
        cliente, veiculo, ordem = await _persistir_cliente_veiculo_os(session)
        await session.commit()

    async with factory() as session:
        usuario_repo = UsuarioRepo(session)
        usuario_recuperado = await usuario_repo.obter_por_email(usuario.email)
        perfis = await usuario_repo.listar_perfis_do_usuario(usuario.id)
        cliente_recuperado = await ClienteRepo(session).obter_por_id(cliente.id)
        veiculo_recuperado = await VeiculoRepo(session).obter_por_placa("ABC1D23")
        ordem_recuperada = await OrdemServicoRepo(session).obter_por_id(ordem.id)

    assert usuario_recuperado == usuario
    assert perfis == ["Mecanico"]
    assert cliente_recuperado == cliente
    assert veiculo_recuperado == veiculo
    assert ordem_recuperada == ordem


@pytest.mark.parametrize("status", list(StatusOrdemServico))
async def test_persiste_todos_os_estados_publicos_da_os(
    db_engine: AsyncEngine,
    status: StatusOrdemServico,
) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        _, _, ordem = await _persistir_cliente_veiculo_os(session, status=status)
        await session.commit()

    async with factory() as session:
        recuperada = await OrdemServicoRepo(session).obter_por_id(ordem.id)

    assert recuperada is not None
    assert recuperada.status is status


async def test_persiste_snapshots_externos_e_estados_operacionais_dos_itens(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        _, _, ordem = await _persistir_cliente_veiculo_os(session)
        repo = OrdemServicoRepo(session)
        servico_id = uuid4()
        await repo.salvar_servico(
            OrdemServicoServico(
                id=ID.generate(),
                ordem_servico_id=ordem.id.value,
                servico_id=servico_id,
                nome_servico="Alinhamento",
                descricao_servico="Snapshot externo",
                valor_unitario=Decimal("120.50"),
                tempo_estimado_minutos=45,
                tempo_executado_minutos=None,
                observacao=None,
                cancelado=False,
            )
        )
        item_ids: dict[StatusItemNaOS, ID] = {}
        for status in StatusItemNaOS:
            item_id = ID.generate()
            item_ids[status] = item_id
            await repo.salvar_item(
                OrdemServicoItem(
                    id=item_id,
                    ordem_servico_id=ordem.id.value,
                    item_estoque_id=uuid4(),
                    nome_item=f"Filtro {status.value}",
                    tipo_item="PECA",
                    quantidade=1,
                    valor_unitario=Decimal("35.90"),
                    status=status,
                )
            )
        await session.commit()

    async with factory() as session:
        repo = OrdemServicoRepo(session)
        servicos = await repo.listar_servicos_da_os(ordem.id)
        itens = await repo.listar_itens_da_os(ordem.id)

    assert servicos[0].servico_id == servico_id
    assert {item.status for item in itens} == set(StatusItemNaOS)
    assert {item.id for item in itens} == set(item_ids.values())


async def test_constraints_rejeitam_estados_desconhecidos(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        _, _, ordem = await _persistir_cliente_veiculo_os(session)
        await session.commit()
        session.add(
            OrdemServicoItemModel(
                id=uuid4(),
                ordem_servico_id=ordem.id.value,
                item_estoque_id=uuid4(),
                nome_item="Item inválido",
                tipo_item="PECA",
                quantidade=1,
                valor_unitario=Decimal("1.00"),
                status="DESCONHECIDO",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_outbox_e_estrutura_persistente_sem_publicacao(
    db_engine: AsyncEngine,
) -> None:
    now = datetime.now(UTC)
    message = OutboxMessageModel(
        id=uuid4(),
        aggregate_type="OrdemServico",
        aggregate_id=uuid4(),
        event_type="ReserveItem.v1",
        event_version=1,
        payload='{"quantity": 1}',
        correlation_id=uuid4(),
        causation_id=None,
        occurred_at=now,
        published_at=None,
        attempts=0,
        next_attempt_at=now,
    )
    factory = _factory(db_engine)
    async with factory() as session:
        session.add(message)
        await session.commit()

    async with factory() as session:
        recovered = await session.get(OutboxMessageModel, message.id)

    assert recovered is not None
    assert recovered.event_type == "ReserveItem.v1"
    assert recovered.published_at is None
    assert recovered.attempts == 0


async def test_unit_of_work_faz_rollback_integral(db_engine: AsyncEngine) -> None:
    cliente = _cliente()
    factory = _factory(db_engine)

    with pytest.raises(RuntimeError, match="forçar rollback"):
        async with ClienteUnitOfWork(factory()) as uow:
            await uow.cliente_repo.salvar(cliente)
            raise RuntimeError("forçar rollback")

    async with factory() as session:
        assert await ClienteRepo(session).obter_por_id(cliente.id) is None


async def test_use_cases_preservam_metadados_e_avancam_versao_monotonicamente(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        _, _, ordem = await _persistir_cliente_veiculo_os(
            session, status=StatusOrdemServico.RECEBIDA
        )
        await session.commit()

    await IniciarDiagnosticoOrdemServicoUseCase(
        uow=OrdemServicoUnitOfWork(factory())
    ).execute(str(ordem.id))
    await RegistrarDiagnosticoOrdemServicoUseCase(
        uow=OrdemServicoUnitOfWork(factory())
    ).execute(
        str(ordem.id),
        RegistrarDiagnosticoRequest(diagnostico="Diagnóstico persistido"),
    )

    async with factory() as session:
        recuperada = await OrdemServicoRepo(session).obter_por_id(ordem.id)

    assert recuperada is not None
    assert recuperada.version == 3
    assert recuperada.status == StatusOrdemServico.EM_DIAGNOSTICO
    assert recuperada.diagnostico == "Diagnóstico persistido"
    assert recuperada.cliente_id == ordem.cliente_id
    assert recuperada.veiculo_id == ordem.veiculo_id


async def test_controle_otimista_rejeita_lost_update_concorrente(
    db_engine: AsyncEngine,
) -> None:
    factory = _factory(db_engine)
    async with factory() as session:
        _, _, ordem = await _persistir_cliente_veiculo_os(
            session, status=StatusOrdemServico.EM_DIAGNOSTICO
        )
        await session.commit()

    async with factory() as session_a, factory() as session_b:
        repo_a = OrdemServicoRepo(session_a)
        repo_b = OrdemServicoRepo(session_b)
        ordem_a = await repo_a.obter_por_id(ordem.id)
        ordem_b = await repo_b.obter_por_id(ordem.id)
        assert ordem_a is not None and ordem_b is not None

        await repo_a.atualizar(ordem_a.registrar_diagnostico("Diagnóstico A"))
        await session_a.commit()

        with pytest.raises(OrdemServicoConflitoDeVersaoError):
            await repo_b.atualizar(ordem_b.registrar_diagnostico("Diagnóstico B"))
        await session_b.rollback()

    async with factory() as session:
        recuperada = await OrdemServicoRepo(session).obter_por_id(ordem.id)

    assert recuperada is not None
    assert recuperada.version == 2
    assert recuperada.diagnostico == "Diagnóstico A"


async def test_isolamento_inicia_cada_teste_sem_dados(db_session: AsyncSession) -> None:
    count = await db_session.scalar(select(func.count()).select_from(ClienteModel))
    assert count == 0
