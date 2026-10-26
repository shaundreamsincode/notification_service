from alembic import command
from sqlalchemy import inspect


def test_upgrade_then_downgrade_on_an_empty_database(db_engine, alembic_config):
    command.upgrade(alembic_config, "head")
    assert "notifications" in inspect(db_engine).get_table_names()

    command.downgrade(alembic_config, "base")
    assert "notifications" not in inspect(db_engine).get_table_names()

    # Leave the schema at head for the tests that share this database.
    command.upgrade(alembic_config, "head")
    assert "notifications" in inspect(db_engine).get_table_names()
