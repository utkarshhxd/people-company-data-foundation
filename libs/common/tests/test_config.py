"""Where a credential is allowed to come from, and which one wins.

These run in the api image, which is the one that carries both `common` and a
credential worth protecting. There is no separate `common` image to build for
three functions that touch no database.
"""

import pytest
from common.config import FILE_SUFFIX, Settings, read_secret_file, resolve


@pytest.fixture
def secrets_dir(tmp_path, monkeypatch):
    """Point the mounted-secrets lookup somewhere this test owns."""
    directory = tmp_path / "secrets"
    directory.mkdir()
    monkeypatch.setattr("common.config.SECRETS_DIR", directory)
    return directory


def write(path, content: str):
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# reading one file
# --------------------------------------------------------------------------


def test_the_newline_an_editor_added_is_not_part_of_the_password(tmp_path):
    path = write(tmp_path / "pw", "s3cret\n")
    assert read_secret_file(path) == "s3cret"


def test_a_windows_line_ending_is_not_part_of_it_either(tmp_path):
    path = write(tmp_path / "pw", "s3cret\r\n")
    assert read_secret_file(path) == "s3cret"


def test_leading_whitespace_is_kept_because_it_may_be_the_password(tmp_path):
    # Silently changing a credential is worse than failing to read one.
    path = write(tmp_path / "pw", "  s3cret\n")
    assert read_secret_file(path) == "  s3cret"


# --------------------------------------------------------------------------
# precedence
# --------------------------------------------------------------------------


def test_nothing_configured_returns_the_default(secrets_dir, monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    assert resolve("postgres_password", "fallback") == "fallback"


def test_a_file_in_the_secrets_directory_is_found_without_being_asked_for(
    secrets_dir, monkeypatch
):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    write(secrets_dir / "postgres_password", "from-mount")
    assert resolve("postgres_password") == "from-mount"


def test_an_explicit_file_beats_one_that_happens_to_be_mounted(
    secrets_dir, tmp_path, monkeypatch
):
    write(secrets_dir / "postgres_password", "from-mount")
    explicit = write(tmp_path / "explicit", "from-file")
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", str(explicit))
    assert resolve("postgres_password") == "from-file"


def test_the_variable_beats_every_file(secrets_dir, tmp_path, monkeypatch):
    """A stale file in a mounted volume must not override what was just typed."""
    write(secrets_dir / "postgres_password", "from-mount")
    explicit = write(tmp_path / "explicit", "from-file")
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", str(explicit))
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-env")
    assert resolve("postgres_password") == "from-env"


def test_a_missing_file_fails_loudly_rather_than_serving_a_default(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", "/nope/not/here")
    # Falling back to the default would mean starting with the wrong credential
    # and failing later, somewhere less obvious than startup.
    with pytest.raises(FileNotFoundError):
        resolve("postgres_password")


# --------------------------------------------------------------------------
# settings as a whole
# --------------------------------------------------------------------------


def test_settings_picks_up_a_password_supplied_as_a_file(
    secrets_dir, tmp_path, monkeypatch
):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    path = write(tmp_path / "pw", "file-password\n")
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", str(path))
    assert Settings().postgres_password == "file-password"


def test_an_empty_variable_does_not_count_as_an_answer(
    secrets_dir, tmp_path, monkeypatch
):
    """Setting a variable to "" is how Compose clears one inherited from .env.

    The secrets overlay does exactly that to hand the credential over to a file.
    If "" counted as supplied, the file would never be read and the service would
    start with no password at all -- which is how this was found.
    """
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    path = write(tmp_path / "pw", "file-password")
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", str(path))
    assert Settings().postgres_password == "file-password"


def test_an_empty_variable_falls_through_to_a_mounted_secret_too(
    secrets_dir, monkeypatch
):
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    write(secrets_dir / "postgres_password", "mounted-password")
    assert Settings().postgres_password == "mounted-password"


def test_a_port_read_from_a_file_is_still_coerced_to_an_int(
    secrets_dir, tmp_path, monkeypatch
):
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    path = write(tmp_path / "port", "6543\n")
    monkeypatch.setenv(f"POSTGRES_PORT{FILE_SUFFIX}", str(path))
    assert Settings().postgres_port == 6543


def test_the_dsn_is_built_from_whatever_the_password_turned_out_to_be(
    secrets_dir, tmp_path, monkeypatch
):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "pcdf_dev")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "pcdf")
    path = write(tmp_path / "pw", "file-password")
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", str(path))
    assert Settings().postgres_dsn.startswith(
        "postgresql://pcdf_dev:file-password@postgres:5432/pcdf?"
    )


def test_a_password_with_url_syntax_in_it_still_reaches_the_right_host(
    secrets_dir, tmp_path, monkeypatch
):
    """The failure this prevents is silent, not loud.

    Unencoded, `p@ss:w/rd#1` re-parses into host `ss`, port `w`, database
    `rd#1@postgres:5432/pcdf` and a one-character password -- a service that
    spends its life dialling a host nobody configured, with no error naming
    the password as the cause.
    """
    import psycopg

    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "pcdf_dev")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "pcdf")
    path = write(tmp_path / "pw", "p@ss:w/rd#1")
    monkeypatch.setenv(f"POSTGRES_PASSWORD{FILE_SUFFIX}", str(path))

    parsed = psycopg.conninfo.conninfo_to_dict(Settings().postgres_dsn)
    assert parsed["host"] == "postgres"
    assert parsed["port"] == "5432"
    assert parsed["dbname"] == "pcdf"
    assert parsed["user"] == "pcdf_dev"
    assert parsed["password"] == "p@ss:w/rd#1"


def test_every_connection_carries_bounded_waits(monkeypatch):
    """A Postgres that accepts the connection and then stops answering must
    not be able to hold a caller forever."""
    import psycopg

    parsed = psycopg.conninfo.conninfo_to_dict(Settings().postgres_dsn)
    assert parsed["connect_timeout"] == "10"
    assert parsed["options"] == "-c statement_timeout=300000"


def test_migrations_can_lift_the_statement_timeout(monkeypatch):
    """Building an index over a large table is a legitimate long statement."""
    import psycopg

    parsed = psycopg.conninfo.conninfo_to_dict(
        Settings().dsn(statement_timeout_ms=0, application_name="pcdf-migrate")
    )
    assert parsed["options"] == "-c statement_timeout=0"
    assert parsed["application_name"] == "pcdf-migrate"
