import asyncio

import psycopg
from common.config import settings
from confluent_kafka.admin import AdminClient


async def check_postgres() -> None:
    """Raises if Postgres is unreachable or doesn't respond to a trivial query."""
    async with await psycopg.AsyncConnection.connect(
        settings.postgres_dsn, connect_timeout=5
    ) as conn, conn.cursor() as cur:
        await cur.execute("SELECT 1")
        await cur.fetchone()


def _list_kafka_topics() -> None:
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    # Raises on timeout/connection failure.
    admin.list_topics(timeout=5)


async def check_kafka() -> None:
    """Raises if the Kafka broker doesn't respond to a metadata request."""
    await asyncio.to_thread(_list_kafka_topics)
