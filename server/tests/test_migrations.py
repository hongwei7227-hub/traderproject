"""The migrations must describe the same schema as the models.

Drift between the two is a classic and quiet failure: the models say one thing,
the database says another, and nothing complains until a query references a
column that migrations never created. Autogenerate reduces the risk but does
not remove it — a hand-edited migration, a forgotten revision, a column added
to a model in a hurry.

Read statically, so this holds without alembic installed and without a database
to run against.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kairos.adapters.persistence.entities import Base

MIGRATIONS = (
    Path(__file__).resolve().parent.parent / "kairos" / "migrations" / "versions"
)


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9]*.py"))


def _string_of(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def declared_tables(source: Path) -> dict[str, set[str]]:
    """Tables and columns a migration creates, read from its `upgrade`."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    tables: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "create_table" or not node.args:
            continue

        table = _string_of(node.args[0])
        if table is None:
            continue

        columns: set[str] = set()
        for argument in node.args[1:]:
            # sa.Column("name", ...) — the first positional is the column name.
            if (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr == "Column"
                and argument.args
                and (name := _string_of(argument.args[0]))
            ):
                columns.add(name)
            # *_timestamps() — a helper contributing the audit columns.
            elif isinstance(argument, ast.Starred):
                columns.update({"created_at", "updated_at"})
        tables[table] = columns

    return tables


def migrated_schema() -> dict[str, set[str]]:
    schema: dict[str, set[str]] = {}
    for source in migration_files():
        schema.update(declared_tables(source))
    return schema


def model_schema() -> dict[str, set[str]]:
    return {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.sorted_tables
    }


class TestMigrationsExist:
    def test_there_is_at_least_one(self) -> None:
        # Otherwise every comparison below passes vacuously.
        assert migration_files()

    def test_revisions_are_unique(self) -> None:
        revisions = []
        for source in migration_files():
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "revision"
                    and node.value is not None
                ):
                    revisions.append(_string_of(node.value))
        assert len(revisions) == len(set(revisions)), f"duplicate revisions: {revisions}"


class TestSchemaAgreement:
    def test_every_model_has_a_table(self) -> None:
        missing = sorted(set(model_schema()) - set(migrated_schema()))
        assert not missing, (
            f"models define {', '.join(missing)} but no migration creates them; "
            "the application would query a table that does not exist"
        )

    def test_no_migration_creates_an_orphan_table(self) -> None:
        orphans = sorted(set(migrated_schema()) - set(model_schema()))
        assert not orphans, (
            f"migrations create {', '.join(orphans)}, which no model describes"
        )

    @pytest.mark.parametrize("table", sorted(model_schema()))
    def test_columns_agree(self, table: str) -> None:
        modelled = model_schema()[table]
        migrated = migrated_schema().get(table, set())

        assert not (modelled - migrated), (
            f"{table}: models declare {sorted(modelled - migrated)} "
            "but no migration creates them"
        )
        assert not (migrated - modelled), (
            f"{table}: migrations create {sorted(migrated - modelled)} "
            "but no model describes them"
        )


class TestReversibility:
    def test_the_initial_migration_drops_what_it_creates(self) -> None:
        """A migration that cannot be undone is a one-way door.

        Not every migration can be reversed — a dropped column's data is
        gone — but a table creation always can, and leaving that out turns a
        bad deploy into a manual recovery.
        """
        source = migration_files()[0]
        created = set(declared_tables(source))

        tree = ast.parse(source.read_text(encoding="utf-8"))
        dropped: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Tuple):
                dropped.update(
                    name for element in node.elts if (name := _string_of(element))
                )

        assert created <= dropped, (
            f"{source.name} creates {sorted(created - dropped)} without dropping them"
        )
