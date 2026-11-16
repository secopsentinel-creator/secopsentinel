from pathlib import Path

from app.core.config import Settings


def test_database_urls_use_separate_users():
    settings = Settings(
        _env_file=None,
        db_host="127.0.0.1",
        db_port=3306,
        db_socket=None,
        db_name="secopsentinel",
        db_user="app",
        db_password="app pw",
        db_migration_user="migrate",
        db_migration_password="migrate/pw",
    )

    assert (
        settings.database_url
        == "mysql+pymysql://app:app+pw@127.0.0.1:3306/secopsentinel?charset=utf8mb4"
    )
    assert (
        settings.database_migration_url
        == "mysql+pymysql://migrate:migrate%2Fpw@127.0.0.1:3306/secopsentinel"
        "?charset=utf8mb4"
    )


def test_paths_expand_home():
    settings = Settings(
        _env_file=None,
        data_dir="~/secopsentinel-data",
        cases_dir="~/secopsentinel-data/cases",
        quarantine_dir="",
    )

    assert settings.data_dir == Path.home() / "secopsentinel-data"
    assert settings.cases_dir == Path.home() / "secopsentinel-data" / "cases"
    assert settings.quarantine_dir is None
