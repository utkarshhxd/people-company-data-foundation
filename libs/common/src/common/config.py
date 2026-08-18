"""Settings, and where a credential is allowed to come from.

Every setting can be given directly as an environment variable, or indirectly as
`<NAME>_FILE` pointing at a file to read it from. The indirection exists for the
credentials: an environment variable is visible in `docker inspect`, in the
process environment of anything running in the container, and in the shell
history of whoever exported it. A file is visible to whoever can read the file.

That is exactly the shape Docker Compose secrets, Kubernetes secret volumes and
systemd credentials all deliver, so this is the seam that lets a real secret
store be put in front of this without any of the services knowing.

`/run/secrets` is read without being asked to, because that is where Compose and
Kubernetes both mount them and a convention that has to be configured is one
that gets skipped.
"""

import os
from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Where Docker Compose and Kubernetes both mount secret files. A file here named
# after a setting supplies that setting, with no configuration at all.
SECRETS_DIR = Path(os.environ.get("PCDF_SECRETS_DIR", "/run/secrets"))

FILE_SUFFIX = "_FILE"


def read_secret_file(path: str | Path) -> str:
    """One secret, from a file, with the trailing newline an editor added removed.

    Not stripped of leading whitespace: a password may legitimately begin with a
    space, and silently changing a credential is worse than failing to read it.
    """
    return Path(path).read_text(encoding="utf-8").rstrip("\r\n")


def resolve(name: str, default: str | None = None) -> str | None:
    """The value of one setting, whichever of the three ways it was supplied.

    Precedence is most explicit first: `NAME` beats `NAME_FILE`, which beats a
    file sitting in the secrets directory. Anything else would mean a stale file
    in a mounted volume silently overriding what an operator just typed.
    """
    direct = os.environ.get(name.upper())
    if direct:
        return direct

    from_file = os.environ.get(f"{name.upper()}{FILE_SUFFIX}")
    if from_file:
        return read_secret_file(from_file)

    mounted = SECRETS_DIR / name.lower()
    if mounted.is_file():
        return read_secret_file(mounted)

    return default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "postgres"

    kafka_bootstrap_servers: str = "localhost:9092"

    log_level: str = "info"

    @model_validator(mode="before")
    @classmethod
    def _from_files(cls, values: Any) -> Any:
        """Fill in anything supplied as a file rather than as a variable.

        Runs before validation so a port read from a file is still coerced to an
        int by the same rules as one read from the environment.
        """
        if not isinstance(values, dict):
            return values
        # An empty variable does not count as supplied. Setting one to "" is the
        # only way Compose can clear a value inherited from .env, and it is
        # exactly what the secrets overlay does to hand a credential over to a
        # file -- so treating "" as an answer would mean the file is never read
        # and the service starts with no password at all.
        supplied = {
            key.lower() for key, value in values.items() if value not in (None, "")
        }
        for name in cls.model_fields:
            if name in supplied:
                continue
            found = resolve(name)
            if found is not None:
                values[name] = found
        return values

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
