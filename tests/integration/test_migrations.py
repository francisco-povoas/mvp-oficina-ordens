from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_alembic_aplica_schema_independente_desde_banco_vazio(
    db_engine: AsyncEngine,
) -> None:
    async with db_engine.connect() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))

        def inspect_schema(sync_connection):
            inspector = inspect(sync_connection)
            return {
                "tables": set(inspector.get_table_names()),
                "order_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("ordem_servico")
                },
                "item_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("ordem_servico_item")
                },
                "item_fks": inspector.get_foreign_keys("ordem_servico_item"),
                "service_fks": inspector.get_foreign_keys("ordem_servico_servico"),
                "outbox_indexes": {
                    index["name"] for index in inspector.get_indexes("outbox_message")
                },
                "outbox_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("outbox_message")
                },
                "outbox_columns": {
                    column["name"] for column in inspector.get_columns("outbox_message")
                },
                "inbox_message_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("inbox_message")
                },
                "inbox_effect_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("inbox_effect")
                },
                "inbox_indexes": {
                    index["name"] for index in inspector.get_indexes("inbox_message")
                },
                "saga_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints("reserva_item_saga")
                },
                "saga_indexes": {
                    index["name"]
                    for index in inspector.get_indexes("reserva_item_saga")
                },
                "saga_fks": inspector.get_foreign_keys("reserva_item_saga"),
                "operation_checks": {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints(
                        "distributed_operation"
                    )
                },
                "operation_indexes": {
                    index["name"]
                    for index in inspector.get_indexes("distributed_operation")
                },
                "operation_fks": inspector.get_foreign_keys(
                    "distributed_operation"
                ),
            }

        schema = await connection.run_sync(inspect_schema)

    expected_tables = {
        "alembic_version",
        "perfil",
        "usuario",
        "usuario_perfil",
        "cliente",
        "veiculo",
        "ordem_servico",
        "ordem_servico_servico",
        "ordem_servico_item",
        "outbox_message",
        "inbox_message",
        "inbox_effect",
        "reserva_item_saga",
        "distributed_operation",
    }
    assert revision == "20261112_06_dist_op"
    assert schema["tables"] == expected_tables
    assert "ck_ordem_status_publico" in schema["order_checks"]
    assert {
        "ck_os_item_quantidade_positiva",
        "ck_os_item_valor_nao_negativo",
        "ck_os_item_status_operacional",
    } <= schema["item_checks"]
    assert {fk["referred_table"] for fk in schema["item_fks"]} == {"ordem_servico"}
    assert {fk["referred_table"] for fk in schema["service_fks"]} == {"ordem_servico"}
    assert "ix_outbox_pending" in schema["outbox_indexes"]
    assert "ix_outbox_claimable" in schema["outbox_indexes"]
    assert {
        "ck_outbox_claim_completo",
        "ck_outbox_claim_expiracao_valida",
        "ck_outbox_falha_com_erro",
    } <= schema["outbox_checks"]
    assert {
        "next_attempt_at",
        "claim_id",
        "claimed_by",
        "claimed_at",
        "claim_expires_at",
        "last_error",
        "last_failed_at",
    } <= schema["outbox_columns"]
    assert "saga_id" in schema["outbox_columns"]
    assert {
        "ck_inbox_message_version",
        "ck_inbox_receive_count_positivo",
        "ck_inbox_message_status",
        "ck_inbox_message_processing",
    } <= schema["inbox_message_checks"]
    assert {"ck_inbox_effect_status", "ck_inbox_effect_processing"} <= schema[
        "inbox_effect_checks"
    ]
    assert {"ix_inbox_message_business_key", "ix_inbox_message_operation_id"} <= schema[
        "inbox_indexes"
    ]
    assert {
        "ck_reserva_saga_quantity_positiva",
        "ck_reserva_saga_version_positiva",
        "ck_reserva_saga_estado",
        "ck_reserva_saga_comando",
        "ck_reserva_saga_resultado_terminal",
        "ck_reserva_saga_rejeicao",
        "ck_reserva_saga_terminal",
        "ck_reserva_saga_idempotency_key",
        "ck_reserva_saga_request_hash",
        "ck_reserva_saga_snapshot_valor",
    } <= schema["saga_checks"]
    assert {fk["referred_table"] for fk in schema["saga_fks"]} == {"ordem_servico"}
    assert {
        "ix_reserva_item_saga_operation_id",
        "ix_reserva_item_saga_ordem_servico_id",
        "ix_reserva_item_saga_estado",
    } <= schema["saga_indexes"]
    assert {
        "ck_distributed_operation_quantity",
        "ck_distributed_operation_version",
        "ck_distributed_operation_type",
        "ck_distributed_operation_state",
        "ck_distributed_operation_terminal_result",
        "ck_distributed_operation_request_hash",
        "ck_distributed_operation_business_key",
        "ck_distributed_operation_lifecycle",
        "ck_distributed_operation_result_metadata",
        "ck_distributed_operation_command_contract",
        "ck_distributed_operation_result_contract",
        "ck_distributed_operation_terminal_contract",
    } <= schema["operation_checks"]
    assert {
        "ix_distributed_operation_ordem_servico_id",
        "ix_distributed_operation_ordem_servico_item_id",
        "ix_distributed_operation_state",
        "ix_distributed_operation_reservation_id",
    } <= schema["operation_indexes"]
    assert {fk["referred_table"] for fk in schema["operation_fks"]} == {
        "ordem_servico",
        "ordem_servico_item",
    }


async def test_schema_nao_contem_tabelas_ou_fks_de_outros_servicos(
    db_engine: AsyncEngine,
) -> None:
    async with db_engine.connect() as connection:
        def inspect_boundaries(sync_connection):
            inspector = inspect(sync_connection)
            tables = set(inspector.get_table_names())
            foreign_keys = {
                (table, fk["referred_table"])
                for table in tables
                for fk in inspector.get_foreign_keys(table)
            }
            return tables, foreign_keys

        tables, foreign_keys = await connection.run_sync(inspect_boundaries)

    assert tables.isdisjoint(
        {"catalogo_servico", "item_estoque", "reserva_estoque", "orcamento", "pagamento"}
    )
    assert all(target in tables for _, target in foreign_keys)
