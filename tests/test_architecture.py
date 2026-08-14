"""The dependency rules, enforced.

An architecture described in a document is a wish. These tests are what makes
it a constraint: the domain core cannot learn about the web framework or the
database, and dependencies cannot start pointing outward, because doing either
turns the suite red.

Implemented by reading the imports rather than by importing anything, so a
violation is reported as a violation instead of as an ImportError from a
package that happens not to be installed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = PROJECT_ROOT / "kairos"

# Things the core must not know about. Web framework, database driver, cache
# client, HTTP client: each would tie a business rule to a delivery mechanism.
FORBIDDEN_IN_CORE = frozenset(
    {
        "fastapi",
        "starlette",
        "sqlalchemy",
        "alembic",
        "redis",
        "httpx",
        "asyncpg",
        "aiosqlite",
        "pydantic_settings",
    }
)

# Outer layers may import inner ones, never the reverse.
LAYER_ORDER = ("runtime", "api", "adapters", "core")


def modules_under(package: str) -> Iterator[Path]:
    yield from (PACKAGE_ROOT / package).rglob("*.py")


def imports_of(source: Path) -> Iterator[str]:
    """Top-level module names imported by a file."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays within the package
                continue
            if node.module:
                yield node.module.split(".")[0]


def kairos_imports_of(source: Path) -> Iterator[str]:
    """Sub-packages of `kairos` that a file imports."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        module: str | None = None
        if isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("kairos."):
                    yield alias.name.split(".")[1]
            continue
        if module and module.startswith("kairos."):
            yield module.split(".")[1]


def relative_to_package(source: Path) -> str:
    return str(source.relative_to(PACKAGE_ROOT)).replace("\\", "/")


class TestCoreIsFrameworkFree:
    def test_the_core_package_exists(self) -> None:
        # Guards against the rest of this file passing vacuously.
        assert (PACKAGE_ROOT / "core").is_dir()
        assert list(modules_under("core"))

    @pytest.mark.parametrize(
        "source",
        list(modules_under("core")),
        ids=lambda p: relative_to_package(p),
    )
    def test_no_framework_imports(self, source: Path) -> None:
        """A business rule that imports a web framework is not a business rule.

        The point is not purity. It is that the core stays testable without a
        server, a database or a network, and that swapping any of those out
        does not reach into the domain.
        """
        offenders = sorted(FORBIDDEN_IN_CORE & set(imports_of(source)))
        assert not offenders, (
            f"{relative_to_package(source)} imports {', '.join(offenders)}; "
            "move the dependency behind a port in kairos.core and implement "
            "it under kairos.adapters"
        )


class TestDependenciesPointInward:
    @pytest.mark.parametrize(
        "source",
        [p for p in modules_under("core")],
        ids=lambda p: relative_to_package(p),
    )
    def test_core_depends_on_nothing_else(self, source: Path) -> None:
        outward = {
            imported
            for imported in kairos_imports_of(source)
            if imported in {"api", "adapters", "runtime"}
        }
        assert not outward, (
            f"{relative_to_package(source)} imports kairos.{outward.pop()}; "
            "the core must not know what serves or stores it"
        )

    @pytest.mark.parametrize(
        "source",
        [p for p in modules_under("adapters")],
        ids=lambda p: relative_to_package(p),
    )
    def test_adapters_do_not_depend_on_delivery(self, source: Path) -> None:
        # An adapter reaching into the API layer would make the database
        # implementation depend on the shape of an HTTP route.
        outward = {i for i in kairos_imports_of(source) if i in {"api", "runtime"}}
        assert not outward, (
            f"{relative_to_package(source)} imports kairos.{outward.pop()}; "
            "adapters implement ports, they do not call inbound layers"
        )


class TestScopedTablesAreEnforced:
    def test_every_tenant_owned_table_inherits_the_marker(self) -> None:
        """A scoped table that forgets the marker would not be filtered.

        The repository refuses to serve such a table, but only if someone
        notices it should have been scoped. This asserts the intent directly:
        anything carrying a tenant column must declare itself scoped.
        """
        from kairos.adapters.persistence.entities import Base, ScopedEntity

        unmarked = [
            mapper.class_.__name__
            for mapper in Base.registry.mappers
            if "tenant_id" in mapper.class_.__table__.columns
            and not issubclass(mapper.class_, ScopedEntity)
            # `tenants` and `members` key on the tenant rather than belonging
            # to one; they are the definition, not an instance of it.
            and mapper.class_.__tablename__ not in {"tenants", "members"}
        ]
        assert not unmarked, (
            f"{', '.join(unmarked)} carry tenant_id but do not inherit "
            "ScopedEntity, so repositories will not filter them"
        )

    def test_scoped_tables_index_the_tenant_first(self) -> None:
        """Queries always filter on the tenant, so it belongs at the front.

        A composite index that leads with anything else cannot serve the
        query that every single read performs.
        """
        from kairos.adapters.persistence.entities import Base, ScopedEntity

        wrong: list[str] = []
        for mapper in Base.registry.mappers:
            entity = mapper.class_
            if not issubclass(entity, ScopedEntity):
                continue
            for index in entity.__table__.indexes:
                columns = [c.name for c in index.columns]
                if len(columns) > 1 and columns[0] != "tenant_id":
                    wrong.append(f"{entity.__tablename__}.{index.name}")
        assert not wrong, f"composite indexes not leading with tenant_id: {wrong}"

    def test_every_scoped_table_has_a_usable_tenant_index(self) -> None:
        """Something must lead with `tenant_id` — index or unique constraint.

        Stated as a requirement rather than as a standalone index on the
        mixin, because every one of these tables already has a composite that
        starts there. Adding a single-column index on top would be redundant,
        and redundant indexes are paid for on every insert and update.
        """
        from sqlalchemy import UniqueConstraint

        from kairos.adapters.persistence.entities import Base, ScopedEntity

        unserved: list[str] = []
        for mapper in Base.registry.mappers:
            entity = mapper.class_
            if not issubclass(entity, ScopedEntity):
                continue
            table = entity.__table__

            leads = [
                [c.name for c in index.columns][:1] == ["tenant_id"]
                for index in table.indexes
            ] + [
                [c.name for c in constraint.columns][:1] == ["tenant_id"]
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            ]
            if not any(leads):
                unserved.append(table.name)

        assert not unserved, (
            f"{', '.join(unserved)} have no index leading with tenant_id, so "
            "the predicate every read applies would fall back to a scan"
        )
