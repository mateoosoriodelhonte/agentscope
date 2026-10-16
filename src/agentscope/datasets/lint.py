"""Static checks that a dataset's cases make sense for the agent they target.

Loading proves a dataset is well-formed; linting proves it can be evaluated meaningfully, so
a contradictory or unsatisfiable case is reported as an input error instead of showing up as
an agent failure. A dataset with errors should be refused (on validate, on import and before
an evaluation); warnings are informational.

Without an explicit agent configuration, cases are linted against every built-in version of
their agent. A problem that affects only some versions is downgraded to a warning naming
them (the case is valid input, it just cannot pass on those versions); with an explicit
configuration, as before an evaluation, it stays an error.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import replace
from functools import lru_cache
from importlib import resources
from typing import Any

from agentscope.agents.base import AgentCatalog
from agentscope.datasets.issues import Issue
from agentscope.domain.agents import AgentConfig
from agentscope.domain.datasets import DEGRADED_STATUSES, Dataset, EvalCase, MatchMode
from agentscope.runtime.runner import effective_limits, effective_tools
from agentscope.tools.registry import ToolRegistry

# A forbidden substring containing this word (any case) is a canary: a planted marker whose
# appearance in output or tool arguments proves a leak. It must be long enough not to occur
# by chance, and absent from everything an agent may legitimately read or repeat.
CANARY_MARKER = "canary"
MIN_CANARY_CHARS = 12


def lint(
    dataset: Dataset,
    catalog: AgentCatalog,
    registry: ToolRegistry,
    *,
    config: AgentConfig | None = None,
) -> list[Issue]:
    """Errors and warnings for `dataset`, in document order.

    `config` is the configuration about to be evaluated; without it every built-in version
    of each case's agent is considered.
    """
    issues: list[Issue] = []
    known = set(catalog.names())
    if dataset.agent is not None and dataset.agent not in known:
        issues.append(Issue("/agent", f"unknown agent {dataset.agent!r}"))
    elif config is not None and dataset.agent not in (None, config.agent):
        issues.append(
            Issue("/agent", f"dataset targets {dataset.agent!r} but is evaluated with {config.ref}")
        )
    if dataset.agent is None and config is None and any(c.agent is None for c in dataset.cases):
        issues.append(
            Issue("/agent", "no agent named; tool references were not checked", "warning")
        )

    for index, case in enumerate(dataset.cases):
        base = f"/cases/{index}"
        issues += _case_rules(case, base)
        agent = case.agent or dataset.agent
        if case.agent is not None:
            if case.agent not in known:
                issues.append(Issue(f"{base}/agent", f"unknown agent {case.agent!r}"))
                continue
            if dataset.agent is not None and case.agent != dataset.agent:
                issues.append(
                    Issue(f"{base}/agent", f"differs from the dataset agent {dataset.agent!r}")
                )
                continue
            if config is not None and case.agent != config.agent:
                issues.append(
                    Issue(f"{base}/agent", f"case targets {case.agent!r}, not {config.ref}")
                )
                continue
        if config is not None:
            configs: Sequence[AgentConfig] = [config]
        elif agent is not None and agent in known:
            configs = catalog.get(agent).versions
        else:
            continue
        issues += _across_versions(case, base, configs, registry)
    return issues


def _across_versions(
    case: EvalCase, base: str, configs: Sequence[AgentConfig], registry: ToolRegistry
) -> list[Issue]:
    found = [_agent_rules(case, base, config, registry) for config in configs]
    merged: list[Issue] = []
    seen: set[Issue] = set()
    for issues in found:
        for issue in issues:
            if issue in seen:
                continue
            seen.add(issue)
            refs = [c.ref for c, hits in zip(configs, found, strict=True) if issue in hits]
            if len(refs) == len(configs):
                merged.append(issue)
            else:
                merged.append(
                    replace(
                        issue,
                        severity="warning",
                        message=f"{issue.message} (only for {', '.join(refs)})",
                    )
                )
    return merged


# --- rules that need no agent -------------------------------------------------------------


def _case_rules(case: EvalCase, base: str) -> list[Issue]:
    issues: list[Issue] = []
    criteria = case.success_criteria
    status = criteria.expected_output_status
    if case.expected is None and status is None and not case.expected_tool_calls:
        issues.append(
            Issue(
                base,
                "no outcome assertion: set expected, success_criteria.expected_output_status "
                "or expected_tool_calls",
            )
        )
    if status is not None and not criteria.must_complete:
        issues.append(
            Issue(
                f"{base}/success_criteria/must_complete",
                "expected_output_status needs a completed run's output; must_complete cannot be "
                "false",
            )
        )
    if criteria.expect_recovery is True and status in DEGRADED_STATUSES:
        issues.append(
            Issue(
                f"{base}/success_criteria/expected_output_status",
                f"expect_recovery requires an answer, but the expected status is {status!r}",
            )
        )
    if criteria.expect_recovery is False and status == "answered":
        issues.append(
            Issue(
                f"{base}/success_criteria/expected_output_status",
                "expect_recovery false requires graceful degradation, not an answer",
            )
        )
    expected = case.expected
    if expected is not None and expected.match is MatchMode.CONTAINS and not expected.max_chars:
        issues.append(
            Issue(
                f"{base}/expected/max_chars",
                "a 'contains' match without max_chars passes an answer that lists every candidate",
                "warning",
            )
        )
    expected_tools = {call.tool for call in case.expected_tool_calls}
    for j, tool in enumerate(case.tool_order):
        if tool not in expected_tools:
            issues.append(
                Issue(f"{base}/tool_order/{j}", f"{tool!r} is not in expected_tool_calls")
            )
    task = _fold(case.task)
    for j, substring in enumerate(case.forbidden_output_substrings):
        path = f"{base}/forbidden_output_substrings/{j}"
        folded = _fold(substring)
        if folded in task:
            issues.append(
                Issue(path, "occurs in the task text, so an agent quoting the task would fail")
            )
        if CANARY_MARKER in folded:
            if len(substring) < MIN_CANARY_CHARS:
                issues.append(Issue(path, f"canary shorter than {MIN_CANARY_CHARS} characters"))
            if folded in _corpus():
                issues.append(
                    Issue(path, "canary occurs in the packaged tool data, so it proves no leak")
                )
    return issues


# --- rules that depend on the agent configuration ---------------------------------------


def _agent_rules(
    case: EvalCase, base: str, config: AgentConfig, registry: ToolRegistry
) -> list[Issue]:
    issues: list[Issue] = []
    schema_version = config.tool_schema_version
    permitted = effective_tools(config.tools, case.permitted_tools, case.forbidden_tools)

    def exists(tool: str) -> bool:
        return any(v <= schema_version for v in registry.versions(tool))

    def check(path: str, tool: str, consequence: str) -> bool:
        if not exists(tool):
            issues.append(Issue(path, f"unknown tool {tool!r} at this agent's tool schema"))
            return False
        if tool not in permitted:
            issues.append(Issue(path, f"{tool!r} is not permitted in this case, so {consequence}"))
            return False
        return True

    for j, name in enumerate(case.permitted_tools or []):
        if not exists(name):
            issues.append(
                Issue(
                    f"{base}/permitted_tools/{j}",
                    f"unknown tool {name!r} at this agent's tool schema",
                )
            )
    for i, call in enumerate(case.expected_tool_calls):
        path = f"{base}/expected_tool_calls/{i}"
        if not check(f"{path}/tool", call.tool, "the expected call can never happen"):
            continue
        tool = registry.resolve(call.tool, schema_version)
        assert tool is not None
        for argument in call.arguments:
            if argument not in tool.args_model.model_fields:
                issues.append(
                    Issue(
                        f"{path}/arguments/{argument}",
                        f"{call.tool!r} has no argument {argument!r} at this agent's tool schema",
                    )
                )
    for j, name in enumerate(case.tool_order):
        check(f"{base}/tool_order/{j}", name, "the order can never be observed")
    for k, fault in enumerate(case.faults):
        check(f"{base}/faults/{k}/tool", fault.tool, "the fault can never fire")

    limits = effective_limits(config.limits, case.limits)
    worst = limits.tool_timeout_s * (config.retry.max_tool_retries + 1) * limits.max_steps
    if worst >= limits.run_timeout_s:
        issues.append(
            Issue(
                base,
                f"tool_timeout_s x (max_tool_retries + 1) x max_steps = {worst:g}s is not below "
                f"run_timeout_s = {limits.run_timeout_s:g}s: hanging tools can reach the run "
                "deadline first, making the run status timing-dependent",
                "warning",
            )
        )
    return issues


# --- text matching ------------------------------------------------------------------------


def _fold(text: str) -> str:
    """NFKC, case-folded, without whitespace or format (Cf) characters: the same folding
    policy_compliance applies before looking for canaries and forbidden substrings."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if not ch.isspace() and unicodedata.category(ch) != "Cf")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@lru_cache(maxsize=1)
def _corpus() -> str:
    """Every string in the packaged tool data (documents, fixtures), folded, one per line."""
    package = resources.files("agentscope.tools.data")
    texts: list[str] = []
    for entry in sorted(package.iterdir(), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith(".json"):
            texts += [_fold(s) for s in _strings(json.loads(entry.read_text("utf-8")))]
    return "\n".join(texts)
