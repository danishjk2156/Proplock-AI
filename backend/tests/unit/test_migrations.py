from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[2]


def test_migration_upgrades_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False

    command.upgrade(config, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"users", "properties", "mandates", "listings", "listing_versions", "audits", "outbox_jobs"} <= tables
    assert {"closings", "ledger_entries", "invoices", "webhook_events", "disputes"} <= tables
    assert "visits" in tables

    command.downgrade(config, "base")
    assert set(inspect(create_engine(url)).get_table_names()) == {"alembic_version"}
