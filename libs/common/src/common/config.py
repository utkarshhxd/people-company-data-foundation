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
from urllib.parse import quote

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

    log_level: str = "info"

    # Copy WARNING and above into `service_log` as well as stdout, so the
    # console can show what the services said. Off by default: a CLI run has no
    # console watching it, and a short-lived process paying for a writer thread
    # and a connection to log two lines is a bad trade. The long-running
    # services turn it on in docker-compose.yml.
    log_to_database: bool = False
    log_database_max_rows: int = 20_000

    # Stop taking new work in when the things that would process it are not
    # there. Liveness is the age of a heartbeat, not the existence of a
    # container: a consumer wedged on a broker that accepts connections and
    # never delivers keeps its container up and does nothing.
    #
    # Three minutes because the consumers beat about once a second and the
    # watcher once a sweep -- a service quiet for three minutes has stopped
    # turning its loop, not had a slow moment.
    supervisor_enabled: bool = True
    supervisor_interval_seconds: float = 30.0
    supervisor_stale_after_seconds: float = 180.0
    # An automatic stop reverses itself once the dependency is back and has
    # stayed back. Deliberately unlike the validation breaker and unlike a
    # person's pause, neither of which ever clear themselves: those are
    # judgements about the data and need a human, this is a reflex about a
    # missing process and is re-checkable. Set false to require a human either
    # way.
    supervisor_auto_resume: bool = True
    supervisor_healthy_checks_before_resume: int = 3

    # Kafka is a notification layer, never the source of truth: events carry
    # references to committed rows, and a consumer reads the values back out of
    # Postgres. See common.events.
    kafka_bootstrap_servers: str = "localhost:9092"

    # Named on every connection so `pg_stat_activity` says which service is
    # holding a lock. Overridden per service at startup; the default is only
    # what an unconfigured process reports.
    application_name: str = "pcdf"
    # Bounded waits, both. A database that accepts the connection and then
    # stops answering is the failure mode these exist for: without a limit the
    # caller waits forever, and a request handler that waits forever is a
    # worker that never serves anyone again. Ten seconds to connect is far more
    # than a healthy local network needs; five minutes for a statement is far
    # more than any query here should take, and still finite.
    connect_timeout_seconds: int = 10
    statement_timeout_ms: int = 300_000

    # The validation circuit breaker. Nothing is lost when validation rejects
    # everything -- each record keeps its payload, its judgements and its
    # quarantine reasons -- but quarantining an entire feed one record at a
    # time is not a useful night's work, and the moment to catch it is early.
    #
    # A rate above a floor, not a count: 50 invalid out of 50,000 is a vendor
    # with messy data, which is the premise of this system; 50 out of 50 is
    # something broken upstream. The floor stops a two-row file from tripping
    # it, because a breaker that cries wolf is one everybody learns to ignore.
    #
    # 0.95 is deliberately close to "everything". RecordsBeingQuarantined is
    # the data-quality alarm; this is the thing that stops the machine.
    validation_breaker_enabled: bool = True
    validation_breaker_threshold: float = 0.95
    validation_breaker_min_records: int = 100

    # A local, self-hosted model -- deliberately not a cloud API, so nothing
    # here sends vendor data anywhere. `ollama_model` defaults to a small model
    # suitable for testing; swap it for a larger one without touching any
    # caller, since every AI-assisted path only ever asks for JSON matching a
    # fixed shape and treats a bad or missing answer as "no opinion".
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout_seconds: float = 60.0
    # Off by default: schema mapping runs inline on the ingest path, so leaving
    # this on unconditionally would make every deployment depend on a reachable
    # model just to load a file. Opt in once Ollama is actually running.
    ai_mapping_enabled: bool = False
    # Reliability of the synthetic source the enrichment job writes through.
    # Deliberately low and below every real vendor's default (0.50): a field
    # with no other reported value may still take an AI guess, but the moment a
    # single vendor reports one, survivorship must prefer the vendor.
    ai_enrichment_reliability: float = 0.20

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

    def dsn(
        self,
        statement_timeout_ms: int | None = None,
        application_name: str | None = None,
    ) -> str:
        """A connection URL that survives whatever the password actually is.

        Every part supplied by an operator is percent-encoded. Without that, a
        password containing `@`, `:`, `/` or `#` -- which is to say most
        generated passwords, and exactly what `.env.example` tells people to
        write into `secrets/postgres_password` -- does not fail: it silently
        re-parses into a different connection. `p@ss:w/rd` yields host `ss`,
        port `w`, and a one-character password, so the service spends its life
        trying to reach a host nobody named.

        `connect_timeout` and `statement_timeout` are here rather than at each
        call site because the failure they prevent is the same everywhere: a
        Postgres that accepts the TCP connection and then never answers holds
        the caller forever, and "forever" in the console is a threadpool worker
        that never comes back. A bounded wait turns that into an error a
        caller can report.

        Both are overridable for the one caller that must not inherit them:
        migrations, where a `CREATE INDEX` over a large table legitimately runs
        longer than any query a service issues, and killing one halfway is
        strictly worse than waiting. Postgres reads `statement_timeout=0` as
        "no limit", so disabling is a value rather than an absent parameter.
        """
        timeout = (
            self.statement_timeout_ms
            if statement_timeout_ms is None
            else statement_timeout_ms
        )
        name = application_name or self.application_name
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_db, safe="")
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
            f"?connect_timeout={self.connect_timeout_seconds}"
            f"&application_name={quote(name, safe='')}"
            f"&options={quote(f'-c statement_timeout={timeout}', safe='')}"
        )

    @property
    def postgres_dsn(self) -> str:
        """The DSN every service connects with, no overrides applied."""
        return self.dsn()


settings = Settings()
