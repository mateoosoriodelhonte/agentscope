"""The evaluator stays independent of the system it grades.

`agentscope.evaluation` may import only the domain contracts, jsonschema, pydantic and the
standard library: if it could reach the runtime, a regression in the guard or the output
validator could make its own verdicts pass. The suite runner orchestrates runs but never
touches storage or the API (callers persist through its hook).
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import agentscope

SRC = Path(agentscope.__file__).parent
ALLOWED_THIRD_PARTY = {"jsonschema", "pydantic"}
ALLOWED_FIRST_PARTY = ("agentscope.domain", "agentscope.evaluation")


def imports(path: Path) -> Iterator[str]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import in {path}"
            assert node.module is not None
            yield node.module


def evaluator_may_import(module: str) -> bool:
    top = module.split(".")[0]
    if top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY:
        return True
    return any(module == p or module.startswith(f"{p}.") for p in ALLOWED_FIRST_PARTY)


def test_rule_rejects_runtime_and_third_party_modules() -> None:
    assert evaluator_may_import("fractions")
    assert evaluator_may_import("agentscope.domain.traces")
    for forbidden in ("agentscope", "agentscope.runtime.guards", "agentscope.tools", "langgraph"):
        assert not evaluator_may_import(forbidden), forbidden


def test_evaluation_imports_only_domain_jsonschema_pydantic_and_stdlib() -> None:
    modules = sorted((SRC / "evaluation").glob("*.py"))
    assert {"checks.py", "engine.py", "gates.py", "metrics.py"} <= {p.name for p in modules}
    violations = [
        f"{path.name}: {module}"
        for path in modules
        for module in imports(path)
        if not evaluator_may_import(module)
    ]
    assert violations == []


def test_suite_runner_does_not_reach_storage_or_api() -> None:
    reached = [
        module
        for module in imports(SRC / "suite_runner.py")
        if module.startswith(("agentscope.storage", "agentscope.api", "agentscope.services"))
    ]
    assert reached == []
