"""Type-strict argument matchers for `tool_arguments`.

A matcher never coerces: `equals: 1` does not match `true` or `"1"`, and `contains` only
ever looks inside strings. Normalization (see `normalize`) only relaxes how strings compare.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentscope.domain.datasets import ArgMatcher
from agentscope.evaluation.normalize import normalize, strict_equal


def matcher_kind(matcher: ArgMatcher) -> str:
    if matcher.contains is not None:
        return "contains"
    if matcher.one_of is not None:
        return "one_of"
    return "equals"


def value_matches(matcher: ArgMatcher, value: Any) -> bool:
    if matcher.contains is not None:
        return isinstance(value, str) and normalize(matcher.contains) in normalize(value)
    if matcher.one_of is not None:
        if isinstance(value, str):
            target = normalize(value)
            return any(isinstance(o, str) and normalize(o) == target for o in matcher.one_of)
        return any(strict_equal(value, option) for option in matcher.one_of)
    return strict_equal(value, matcher.equals)


def argument_matches(matcher: ArgMatcher, arguments: Mapping[str, Any], name: str) -> bool:
    """A missing argument never matches, not even `equals: null`."""
    return name in arguments and value_matches(matcher, arguments[name])
