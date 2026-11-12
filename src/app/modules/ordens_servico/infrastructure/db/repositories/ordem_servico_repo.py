"""Implementação concreta do repositório de Ordens de Serviço (SQLModel + PostgreSQL)."""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import sqlalchemy as sa
from sqlmodel import select

from app.modules.ordens_servico.domain.entities.ordem_servico import (
    OrdemServico,
    StatusOrcamentoFinanceiro,
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
from app.modules.ordens_servico.domain.filters.ordem_servico import ListarOrdensServicoFiltro
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_item_model import (
    OrdemServicoItemModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_model import (
    OrdemServicoModel,
)
from app.modules.ordens_servico.infrastructure.db.models.ordem_servico_servico_model import (
    OrdemServicoServicoModel,
)
from app.shared.infra.db import DBSession
from app.shared.value_objects.id import ID


class OrdemServicoRepo:
    """Repositório da agregação Ordem de Serviço."""

    def __init__(self, session: DBSession) -> None:
        self.session = session

    # -----------------------------------------------------------------------
    # OrdemServico
    # -----------------------------------------------------------------------

    async def salvar(self, os: OrdemServico) -> None:
        model = OrdemServicoModel(
            id=os.id.value,
            cliente_id=os.cliente_id,
            veiculo_id=os.veiculo_id,
            status=os.status.value,
            queixa_inicial=os.queixa_inicial,
            diagnostico=os.diagnostico,
            criado_em=os.criado_em,
            atualizado_em=os.atualizado_em,
            iniciado_diagnostico_em=os.iniciado_diagnostico_em,
            diagnostico_concluido_em=os.diagnostico_concluido_em,
            version=os.version,
            quote_id=os.quote_id,
            quote_status=os.quote_status.value if os.quote_status else None,
            quote_version=os.quote_version,
            quote_total=os.quote_total,
            quote_communicated_at=os.quote_communicated_at,
            quote_decided_at=os.quote_decided_at,
            quote_rejection_reason=os.quote_rejection_reason,
            payment_id=os.payment_id,
            payment_status=os.payment_status,
            payment_amount=os.payment_amount,
            payment_confirmed_at=os.payment_confirmed_at,
            payment_quote_id=os.payment_quote_id,
            payment_quote_version=os.payment_quote_version,
        )
        self.session.add(model)

    async def obter_por_id(self, _id: ID) -> Optional[OrdemServico]:
        model = await self.session.get(OrdemServicoModel, _id.value)
        if not model:
            return None
        return self._os_to_entity(model)

    async def obter_por_id_com_lock(self, _id: ID) -> Optional[OrdemServico]:
        result = await self.session.exec(
            select(OrdemServicoModel)
            .where(OrdemServicoModel.id == _id.value)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        model = result.first()
        return self._os_to_entity(model) if model else None

    async def atualizar(self, os: OrdemServico) -> Optional[OrdemServico]:
        versao_anterior = os.version - 1
        if versao_anterior < 1:
            raise OrdemServicoConflitoDeVersaoError(
                "Atualização da OS deve avançar exatamente uma versão."
            )

        statement = (
            sa.update(OrdemServicoModel)
            .where(
                OrdemServicoModel.id == os.id.value,
                OrdemServicoModel.version == versao_anterior,
            )
            .values(
                status=os.status.value,
                queixa_inicial=os.queixa_inicial,
                diagnostico=os.diagnostico,
                atualizado_em=os.atualizado_em,
                iniciado_diagnostico_em=os.iniciado_diagnostico_em,
                diagnostico_concluido_em=os.diagnostico_concluido_em,
                version=os.version,
                quote_id=os.quote_id,
                quote_status=os.quote_status.value if os.quote_status else None,
                quote_version=os.quote_version,
                quote_total=os.quote_total,
                quote_communicated_at=os.quote_communicated_at,
                quote_decided_at=os.quote_decided_at,
                quote_rejection_reason=os.quote_rejection_reason,
                payment_id=os.payment_id,
                payment_status=os.payment_status,
                payment_amount=os.payment_amount,
                payment_confirmed_at=os.payment_confirmed_at,
                payment_quote_id=os.payment_quote_id,
                payment_quote_version=os.payment_quote_version,
            )
            .execution_options(synchronize_session=False)
        )
        result = await self.session.exec(statement)
        if result.rowcount != 1:
            existente = await self.session.get(OrdemServicoModel, os.id.value)
            if existente is None:
                return None
            raise OrdemServicoConflitoDeVersaoError(
                f"Conflito de versão ao atualizar OS '{os.id}'. "
                f"Esperada: {versao_anterior}; atual: {existente.version}."
            )
        return os

    async def listar(self, filtros: ListarOrdensServicoFiltro) -> list[OrdemServico]:
        query = select(OrdemServicoModel)

        if filtros.status is not None:
            query = query.where(OrdemServicoModel.status == filtros.status.value)
        else:
            query = query.where(
                OrdemServicoModel.status.notin_(
                    (
                        StatusOrdemServico.ENTREGUE.value,
                        StatusOrdemServico.ENCERRADA.value,
                    )
                )
            )
        if filtros.cliente_id:
            query = query.where(OrdemServicoModel.cliente_id == UUID(filtros.cliente_id))
        if filtros.veiculo_id:
            query = query.where(OrdemServicoModel.veiculo_id == UUID(filtros.veiculo_id))
        if filtros.data_inicio:
            query = query.where(OrdemServicoModel.criado_em >= filtros.data_inicio)
        if filtros.data_fim:
            query = query.where(OrdemServicoModel.criado_em <= filtros.data_fim)

        status_order = sa.case(
            (OrdemServicoModel.status == StatusOrdemServico.EM_EXECUCAO.value, 1),
            (OrdemServicoModel.status == StatusOrdemServico.AGUARDANDO_APROVACAO.value, 2),
            (OrdemServicoModel.status == StatusOrdemServico.EM_DIAGNOSTICO.value, 3),
            (OrdemServicoModel.status == StatusOrdemServico.RECEBIDA.value, 4),
            else_=99
        )

        # Sem filtro por status, prioriza o funil operacional; com filtro explicito,
        # o desempate efetivo passa a ser apenas pelas OS mais antigas primeiro.
        query = query.order_by(
            status_order,
            OrdemServicoModel.criado_em.asc()
        )

        result = await self.session.exec(query)
        return [self._os_to_entity(m) for m in result.all()]

    # -----------------------------------------------------------------------
    # OrdemServicoServico
    # -----------------------------------------------------------------------

    async def salvar_servico(self, os_servico: OrdemServicoServico) -> None:
        model = OrdemServicoServicoModel(
            id=os_servico.id.value,
            ordem_servico_id=os_servico.ordem_servico_id,
            servico_id=os_servico.servico_id,
            nome_servico=os_servico.nome_servico,
            descricao_servico=os_servico.descricao_servico,
            valor_unitario=os_servico.valor_unitario,
            tempo_estimado_minutos=os_servico.tempo_estimado_minutos,
            tempo_executado_minutos=os_servico.tempo_executado_minutos,
            observacao=os_servico.observacao,
            cancelado=os_servico.cancelado,
        )
        self.session.add(model)

    async def obter_servico_por_id(self, _id: ID) -> Optional[OrdemServicoServico]:
        model = await self.session.get(OrdemServicoServicoModel, _id.value)
        if not model:
            return None
        return self._oss_to_entity(model)

    async def listar_servicos_da_os(self, ordem_servico_id: ID) -> list[OrdemServicoServico]:
        result = await self.session.exec(
            select(OrdemServicoServicoModel).where(
                OrdemServicoServicoModel.ordem_servico_id == ordem_servico_id.value
            )
        )
        return [self._oss_to_entity(m) for m in result.all()]

    async def atualizar_servico(
        self, os_servico: OrdemServicoServico
    ) -> Optional[OrdemServicoServico]:
        model = await self.session.get(OrdemServicoServicoModel, os_servico.id.value)
        if not model:
            return None
        model.observacao = os_servico.observacao
        model.cancelado = os_servico.cancelado
        model.tempo_executado_minutos = os_servico.tempo_executado_minutos
        self.session.add(model)
        return os_servico

    async def obter_servico_ativo_por_servico_id(
        self, ordem_servico_id: ID, servico_id: ID
    ) -> Optional[OrdemServicoServico]:
        """Retorna o vínculo ativo (cancelado=False) para o par OS+serviço, ou None."""
        result = await self.session.exec(
            select(OrdemServicoServicoModel).where(
                OrdemServicoServicoModel.ordem_servico_id == ordem_servico_id.value,
                OrdemServicoServicoModel.servico_id == servico_id.value,
                OrdemServicoServicoModel.cancelado == False,  # noqa: E712
            )
        )
        model = result.first()
        return self._oss_to_entity(model) if model else None

    # -----------------------------------------------------------------------
    # OrdemServicoItem
    # -----------------------------------------------------------------------

    async def salvar_item(self, os_item: OrdemServicoItem) -> None:
        model = OrdemServicoItemModel(
            id=os_item.id.value,
            ordem_servico_id=os_item.ordem_servico_id,
            item_estoque_id=os_item.item_estoque_id,
            nome_item=os_item.nome_item,
            tipo_item=os_item.tipo_item,
            quantidade=os_item.quantidade,
            valor_unitario=os_item.valor_unitario,
            status=os_item.status.value,
        )
        self.session.add(model)

    async def obter_item_por_id(self, _id: ID) -> Optional[OrdemServicoItem]:
        model = await self.session.get(OrdemServicoItemModel, _id.value)
        if not model:
            return None
        return self._osi_to_entity(model)

    async def listar_itens_da_os(self, ordem_servico_id: ID) -> list[OrdemServicoItem]:
        result = await self.session.exec(
            select(OrdemServicoItemModel).where(
                OrdemServicoItemModel.ordem_servico_id == ordem_servico_id.value
            )
        )
        return [self._osi_to_entity(m) for m in result.all()]

    async def atualizar_item(self, os_item: OrdemServicoItem) -> Optional[OrdemServicoItem]:
        model = await self.session.get(OrdemServicoItemModel, os_item.id.value)
        if not model:
            return None
        model.status = os_item.status.value
        self.session.add(model)
        return os_item

    async def obter_item_ativo_por_item_estoque_id(
        self, ordem_servico_id: ID, item_estoque_id: ID
    ) -> Optional[OrdemServicoItem]:
        """Retorna o vínculo ativo (status != CANCELADO) para o par OS+item, ou None."""
        result = await self.session.exec(
            select(OrdemServicoItemModel).where(
                OrdemServicoItemModel.ordem_servico_id == ordem_servico_id.value,
                OrdemServicoItemModel.item_estoque_id == item_estoque_id.value,
                OrdemServicoItemModel.status != StatusItemNaOS.CANCELADO.value,
            )
        )
        model = result.first()
        return self._osi_to_entity(model) if model else None

    async def obter_item_por_id_com_lock(
        self, _id: ID
    ) -> Optional[OrdemServicoItem]:
        result = await self.session.exec(
            select(OrdemServicoItemModel)
            .where(OrdemServicoItemModel.id == _id.value)
            .with_for_update()
        )
        model = result.first()
        return self._osi_to_entity(model) if model else None

    # -----------------------------------------------------------------------
    # Conversão model → entidade
    # -----------------------------------------------------------------------

    @staticmethod
    def _os_to_entity(model: OrdemServicoModel) -> OrdemServico:
        return OrdemServico(
            id=ID.from_string(str(model.id)),
            cliente_id=model.cliente_id,
            veiculo_id=model.veiculo_id,
            status=StatusOrdemServico(model.status),
            queixa_inicial=model.queixa_inicial,
            diagnostico=model.diagnostico,
            criado_em=model.criado_em,
            atualizado_em=model.atualizado_em,
            iniciado_diagnostico_em=model.iniciado_diagnostico_em,
            diagnostico_concluido_em=model.diagnostico_concluido_em,
            version=model.version,
            quote_id=model.quote_id,
            quote_status=(
                StatusOrcamentoFinanceiro(model.quote_status)
                if model.quote_status
                else None
            ),
            quote_version=model.quote_version,
            quote_total=(
                Decimal(str(model.quote_total))
                if model.quote_total is not None
                else None
            ),
            quote_communicated_at=model.quote_communicated_at,
            quote_decided_at=model.quote_decided_at,
            quote_rejection_reason=model.quote_rejection_reason,
            payment_id=model.payment_id,
            payment_status=model.payment_status,
            payment_amount=(
                Decimal(str(model.payment_amount))
                if model.payment_amount is not None
                else None
            ),
            payment_confirmed_at=model.payment_confirmed_at,
            payment_quote_id=model.payment_quote_id,
            payment_quote_version=model.payment_quote_version,
        )

    @staticmethod
    def _oss_to_entity(model: OrdemServicoServicoModel) -> OrdemServicoServico:
        return OrdemServicoServico(
            id=ID.from_string(str(model.id)),
            ordem_servico_id=model.ordem_servico_id,
            servico_id=model.servico_id,
            nome_servico=model.nome_servico,
            descricao_servico=model.descricao_servico,
            valor_unitario=Decimal(str(model.valor_unitario)),
            tempo_estimado_minutos=model.tempo_estimado_minutos,
            tempo_executado_minutos=model.tempo_executado_minutos,
            observacao=model.observacao,
            cancelado=model.cancelado,
        )

    @staticmethod
    def _osi_to_entity(model: OrdemServicoItemModel) -> OrdemServicoItem:
        return OrdemServicoItem(
            id=ID.from_string(str(model.id)),
            ordem_servico_id=model.ordem_servico_id,
            item_estoque_id=model.item_estoque_id,
            nome_item=model.nome_item,
            tipo_item=model.tipo_item,
            quantidade=model.quantidade,
            valor_unitario=Decimal(str(model.valor_unitario)),
            status=StatusItemNaOS(model.status),
        )

    # -----------------------------------------------------------------------
    # Métricas — tempo de execução por serviço do catálogo
    # -----------------------------------------------------------------------

    _STATUS_FINALIZADAS = ("FINALIZADA", "ENTREGUE")

    async def obter_estatistica_tempo_execucao_servico(
        self, servico_id: UUID
    ) -> dict[str, Any]:
        """Retorna estatística agregada de tempo executado para um serviço do catálogo."""
        stmt = (
            sa.select(
                sa.func.count(OrdemServicoServicoModel.id).label("quantidade"),
                sa.func.avg(OrdemServicoServicoModel.tempo_executado_minutos).label("media"),
                sa.func.min(OrdemServicoServicoModel.tempo_executado_minutos).label("menor"),
                sa.func.max(OrdemServicoServicoModel.tempo_executado_minutos).label("maior"),
            )
            .select_from(OrdemServicoServicoModel)
            .join(
                OrdemServicoModel,
                OrdemServicoModel.id == OrdemServicoServicoModel.ordem_servico_id,
            )
            .where(
                OrdemServicoServicoModel.servico_id == servico_id,
                OrdemServicoServicoModel.cancelado == False,  # noqa: E712
                OrdemServicoServicoModel.tempo_executado_minutos.isnot(None),
                OrdemServicoModel.status.in_(self._STATUS_FINALIZADAS),
            )
        )
        result = await self.session.exec(stmt)
        row = result.one()
        return {
            "quantidade": row.quantidade or 0,
            "media": float(row.media) if row.media is not None else None,
            "menor": int(row.menor) if row.menor is not None else None,
            "maior": int(row.maior) if row.maior is not None else None,
        }

    async def listar_execucoes_servico(
        self, servico_id: UUID
    ) -> list[dict[str, Any]]:
        """Retorna lista de execuções do serviço em OS finalizadas/entregues."""
        stmt = (
            sa.select(
                OrdemServicoModel.id.label("ordem_servico_id"),
                OrdemServicoServicoModel.id.label("ordem_servico_servico_id"),
                OrdemServicoServicoModel.tempo_executado_minutos,
                OrdemServicoModel.status.label("status_os"),
            )
            .select_from(OrdemServicoServicoModel)
            .join(
                OrdemServicoModel,
                OrdemServicoModel.id == OrdemServicoServicoModel.ordem_servico_id,
            )
            .where(
                OrdemServicoServicoModel.servico_id == servico_id,
                OrdemServicoServicoModel.cancelado == False,  # noqa: E712
                OrdemServicoServicoModel.tempo_executado_minutos.isnot(None),
                OrdemServicoModel.status.in_(self._STATUS_FINALIZADAS),
            )
            .order_by(OrdemServicoModel.criado_em.asc())
        )
        result = await self.session.exec(stmt)
        return [
            {
                "ordem_servico_id": str(row.ordem_servico_id),
                "ordem_servico_servico_id": str(row.ordem_servico_servico_id),
                "tempo_executado_minutos": row.tempo_executado_minutos,
                "status_os": row.status_os,
            }
            for row in result.all()
        ]
