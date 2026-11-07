"""Baseline independente de IAM, clientes, veículos e ordens."""

import sqlalchemy as sa

from alembic import op

revision = "20261107_01_ordens"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "perfil",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nome", sa.String(30), nullable=False, unique=True),
        sa.Column("descricao", sa.String(150), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "usuario",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nome", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("senha_hash", sa.String(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "usuario_perfil",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuario.id"), nullable=False),
        sa.Column("perfil_id", sa.Uuid(), sa.ForeignKey("perfil.id"), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("usuario_id", "perfil_id", name="uix_usuario_perfil"),
    )
    op.create_table(
        "cliente",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tipo_pessoa", sa.CHAR(2), nullable=False),
        sa.Column("nome_razao_social", sa.String(150), nullable=False),
        sa.Column("cpf_cnpj", sa.String(14), nullable=False, unique=True),
        sa.Column("telefone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(150)),
        sa.Column("cep", sa.String(8)),
        sa.Column("logradouro", sa.String(150)),
        sa.Column("numero", sa.String(20)),
        sa.Column("complemento", sa.String(100)),
        sa.Column("bairro", sa.String(100)),
        sa.Column("cidade", sa.String(100)),
        sa.Column("uf", sa.CHAR(2)),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "veiculo",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cliente_id", sa.Uuid(), sa.ForeignKey("cliente.id"), nullable=False),
        sa.Column("placa", sa.String(10), nullable=False, unique=True),
        sa.Column("marca", sa.String(60), nullable=False),
        sa.Column("modelo", sa.String(60), nullable=False),
        sa.Column("ano_fabricacao", sa.SmallInteger()),
        sa.Column("ano_modelo", sa.SmallInteger()),
        sa.Column("cor", sa.String(30)),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ordem_servico",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cliente_id", sa.Uuid(), sa.ForeignKey("cliente.id"), nullable=False),
        sa.Column("veiculo_id", sa.Uuid(), sa.ForeignKey("veiculo.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("queixa_inicial", sa.Text(), nullable=False),
        sa.Column("diagnostico", sa.Text()),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("iniciado_diagnostico_em", sa.DateTime(timezone=True)),
        sa.Column("diagnostico_concluido_em", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payment_id", sa.Uuid()),
        sa.Column("payment_status", sa.String(20)),
        sa.Column("payment_amount", sa.Numeric(12, 2)),
        sa.Column("payment_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("payment_quote_version", sa.Integer()),
    )
    op.create_table(
        "ordem_servico_servico",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ordem_servico_id", sa.Uuid(), sa.ForeignKey("ordem_servico.id"), nullable=False),
        sa.Column("servico_id", sa.Uuid(), nullable=False),
        sa.Column("nome_servico", sa.String(100), nullable=False),
        sa.Column("descricao_servico", sa.String(255)),
        sa.Column("valor_unitario", sa.Numeric(10, 2), nullable=False),
        sa.Column("tempo_estimado_minutos", sa.Integer(), nullable=False),
        sa.Column("tempo_executado_minutos", sa.Integer()),
        sa.Column("observacao", sa.Text()),
        sa.Column("cancelado", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_os_servico_catalogo", "ordem_servico_servico", ["servico_id"])
    op.create_table(
        "ordem_servico_item",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ordem_servico_id", sa.Uuid(), sa.ForeignKey("ordem_servico.id"), nullable=False),
        sa.Column("item_estoque_id", sa.Uuid(), nullable=False),
        sa.Column("nome_item", sa.String(100), nullable=False),
        sa.Column("tipo_item", sa.String(10), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("valor_unitario", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
    )
    op.create_index("ix_os_item_estoque", "ordem_servico_item", ["item_estoque_id"])
    op.create_table(
        "outbox_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("causation_id", sa.Uuid()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_outbox_pending", "outbox_message", ["published_at"])


def downgrade() -> None:
    op.drop_table("outbox_message")
    op.drop_table("ordem_servico_item")
    op.drop_table("ordem_servico_servico")
    op.drop_table("ordem_servico")
    op.drop_table("veiculo")
    op.drop_table("cliente")
    op.drop_table("usuario_perfil")
    op.drop_table("usuario")
    op.drop_table("perfil")
