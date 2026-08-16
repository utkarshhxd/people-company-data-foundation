from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from common.config import settings


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.postgres_dsn, autocommit=autocommit) as conn:
        yield conn
