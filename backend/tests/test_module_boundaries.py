"""Architecture guards.

A modular monolith only stays modular if something checks. These tests fail
the build when a module reaches past its boundary, which is cheaper than
discovering a cycle during Phase 5.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"
MODULES_ROOT = APP_ROOT / "modules"

#: Lower numbers may not import higher ones. `accounting` sits below every
#: business module because every module posts *through* it, never into it.
MODULE_LAYERS: dict[str, int] = {
    "platform": 0,
    "audit": 1,
    # `organization` is the tenant root: it defines the boundary that every
    # other record is scoped to, so it sits below identity rather than above.
    "organization": 2,
    "identity": 3,
    "accounting": 4,
    # Sales owns invoices and payments. Cash and banking sit above it because
    # a cash session contains payments and a bank settlement matches them —
    # the container knows its contents, not the other way round.
    "sales": 5,
    "purchasing": 5,
    "inventory": 5,
    "hr": 5,
    "cash": 6,
    "banking": 6,
    "payroll": 7,
    "clinic": 7,
    "expense": 7,
    "document": 7,
    "reporting": 8,
    # Composes organization + identity to create a tenant and its first owner.
    "provisioning": 9,
}

#: No exceptions. An edge that needs one is a sign the use case belongs in a
#: module above both, the way tenant provisioning does.
ALLOWED_UPWARD_EDGES: set[tuple[str, str]] = set()


def _imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)
    return found


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_of(path: pathlib.Path) -> str | None:
    try:
        relative = path.relative_to(MODULES_ROOT)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else None


class TestCoreIsIndependent:
    def test_core_never_imports_a_business_module(self) -> None:
        """`core` is infrastructure. The arrow points one way only."""
        offenders: list[str] = []
        for path in _python_files(APP_ROOT / "core"):
            for name in _imports(path):
                if name.startswith("app.modules"):
                    offenders.append(f"{path.name} imports {name}")
        assert not offenders, "core must not depend on modules: " + "; ".join(offenders)

    def test_core_never_imports_the_api_layer(self) -> None:
        offenders = [
            f"{path.name} imports {name}"
            for path in _python_files(APP_ROOT / "core")
            for name in _imports(path)
            if name.startswith("app.api")
        ]
        assert not offenders, "; ".join(offenders)


class TestModuleLayering:
    def test_every_module_directory_has_a_declared_layer(self) -> None:
        present = {
            p.name for p in MODULES_ROOT.iterdir() if p.is_dir() and not p.name.startswith("__")
        }
        undeclared = present - set(MODULE_LAYERS)
        assert not undeclared, (
            f"add {sorted(undeclared)} to MODULE_LAYERS so its dependencies are checked"
        )

    def test_no_module_imports_a_higher_layer(self) -> None:
        """This is what keeps the dependency graph acyclic."""
        violations: list[str] = []
        for path in _python_files(MODULES_ROOT):
            source = _module_of(path)
            if source is None:
                continue
            for name in _imports(path):
                if not name.startswith("app.modules."):
                    continue
                target = name.split(".")[2]
                if target == source:
                    continue
                if (source, target) in ALLOWED_UPWARD_EDGES:
                    continue
                if source == "provisioning":
                    # Sits above everything by design; see MODULE_LAYERS.
                    continue
                if MODULE_LAYERS.get(target, 99) > MODULE_LAYERS.get(source, 0):
                    violations.append(f"{source} ({path.name}) imports higher layer {target}")
        assert not violations, "; ".join(sorted(set(violations)))

    def test_no_module_imports_another_modules_models(self) -> None:
        """Cross-module reads go through a service, so invariants stay owned.

        `provisioning` is exempt: its whole purpose is to compose two modules
        into one transaction, which it cannot do without their models.
        """
        violations: list[str] = []
        for path in _python_files(MODULES_ROOT):
            source = _module_of(path)
            if source is None:
                continue
            for name in _imports(path):
                if not name.startswith("app.modules."):
                    continue
                parts = name.split(".")
                target = parts[2]
                if target == source or len(parts) < 4:
                    continue
                if source == "provisioning":
                    continue
                if parts[3] == "models" and (source, target) not in ALLOWED_UPWARD_EDGES:
                    violations.append(f"{source} imports {target}.models ({path.name})")
        assert not violations, "; ".join(sorted(set(violations)))


class TestAccountingIsReachedOnlyThroughItsServices:
    def test_no_module_will_import_accounting_models(self) -> None:
        """Guards the rule for Phase 2 onward, before the temptation exists.

        A business module must not write accounting tables directly. It builds
        a posting request and hands it to the accounting service, which is
        where balance, period and idempotency checks live.
        """
        violations = [
            f"{_module_of(path)} imports {name}"
            for path in _python_files(MODULES_ROOT)
            for name in _imports(path)
            if name.startswith("app.modules.accounting.models")
            and _module_of(path) not in (None, "accounting")
        ]
        assert not violations, "; ".join(violations)


class TestNoFloatArithmeticOnMoney:
    @pytest.mark.parametrize(
        "forbidden",
        ["float(", "round(", "math.fsum"],
    )
    def test_money_module_avoids_float_helpers(self, forbidden: str) -> None:
        """`app.core.money` must not reach for a binary-float helper."""
        source = (APP_ROOT / "core" / "money.py").read_text()
        # `float` appears in the rejection path; check only executable use.
        code_lines = [
            line
            for line in source.splitlines()
            if not line.strip().startswith("#") and "isinstance" not in line
        ]
        body = "\n".join(code_lines)
        assert forbidden not in body, f"{forbidden} must not be used on money"
