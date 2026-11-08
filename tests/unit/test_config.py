import pytest

from app.config import Settings


def test_runtime_requires_database_url() -> None:
    settings = Settings(environment="test", database_url="")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        settings.validate_runtime()


def test_production_rejects_unsafe_jwt_secret() -> None:
    settings = Settings(
        environment="prod",
        database_url="postgresql+asyncpg://db/ordens",
        jwt_secret_key="unsafe",
    )

    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        settings.validate_runtime()


def test_publisher_requires_queue_and_lease_longer_than_send_timeout() -> None:
    missing_queue = Settings(
        environment="test",
        database_url="postgresql+asyncpg://db/ordens",
        sqs_ordens_to_recursos_url="",
    )
    with pytest.raises(ValueError, match="SQS_ORDENS_TO_RECURSOS_URL"):
        missing_queue.validate_publisher_runtime()

    short_lease = Settings(
        environment="test",
        database_url="postgresql+asyncpg://db/ordens",
        sqs_ordens_to_recursos_url="http://elasticmq/queue.fifo",
        publisher_lease_seconds=5,
        sqs_send_timeout_seconds=5,
    )
    with pytest.raises(ValueError, match="lease"):
        short_lease.validate_publisher_runtime()


def test_production_publisher_rejects_custom_sqs_endpoint() -> None:
    settings = Settings(
        environment="prod",
        database_url="postgresql+asyncpg://db/ordens",
        jwt_secret_key="segredo-seguro",
        sqs_endpoint_url="http://elasticmq:9324",
        sqs_ordens_to_recursos_url="https://sqs.us-east-1.amazonaws.com/123/queue.fifo",
    )
    with pytest.raises(ValueError, match="SQS_ENDPOINT_URL"):
        settings.validate_publisher_runtime()
