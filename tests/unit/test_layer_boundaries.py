"""Constitution I, enforced: agents/ and tools/ must never import sqlalchemy,
app.models, or app.repositories directly. Agents call tools; tools call services."""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
FORBIDDEN_MODULE_PREFIXES = ("sqlalchemy", "app.models", "app.repositories")
GUARDED_PACKAGES = ("agents", "tools")


def _imported_module_names(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _violations_in_package(package: str) -> list[str]:
    package_dir = APP_ROOT / package
    violations: list[str] = []
    for py_file in package_dir.rglob("*.py"):
        for module_name in _imported_module_names(py_file):
            if module_name.startswith(FORBIDDEN_MODULE_PREFIXES):
                violations.append(f"{py_file.relative_to(APP_ROOT)} imports {module_name!r}")
    return violations


def test_agents_and_tools_never_import_database_layers():
    all_violations: list[str] = []
    for package in GUARDED_PACKAGES:
        all_violations.extend(_violations_in_package(package))

    assert not all_violations, "Layer boundary violated:\n" + "\n".join(all_violations)
