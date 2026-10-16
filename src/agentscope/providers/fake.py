"""Deterministic fake model provider for CI, offline demos, and reproducible evaluation.

This is a *test double*, not a language model. Each agent supplies a "brain": a pure
function from the conversation so far to the next structured decision. The decision is
serialized to text and goes through the same parsing and validation path as a real model's
response. Three profiles simulate behaviour real LLMs are known to exhibit, so the
evaluation pipeline has real failures to detect without any network or GPU:

- ``scripted-careful``: never acts on instructions embedded in tool output; retries a
  failed tool once; reports insufficient evidence instead of guessing.
- ``scripted-naive``: acts on instruction-like text embedded in tool output *unless* that
  output is delimited as untrusted AND the system prompt contains the
  instruction-hierarchy clause; gives up on failed tools and answers anyway.
- ``scripted-compliant``: acts on every embedded instruction, delimited or not — a fully
  compromised model. Used to show that the runtime's permission guard contains the damage
  no matter what the model decides.

These behaviours are documented simulations of failure modes (docs/AGENT_RUNTIME.md), not
claims about any real model. The same input always yields the same output, and no token
usage is reported because none is measured.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agentscope.domain.tools import ToolOutcome, ToolSpec
from agentscope.providers.base import ModelRequest, ModelResponse, ProviderError
from agentscope.runtime.prompts import HIERARCHY_MARKER

Profile = Literal["careful", "naive", "compliant"]
PROFILES: dict[str, Profile] = {
    "scripted-careful": "careful",
    "scripted-naive": "naive",
    "scripted-compliant": "compliant",
}
# Bumped whenever any brain's decisions change; part of the effective config hash.
# 2: the research brain no longer retries a call that tool output asked for or that the
#    runtime refused (an unknown or unpermitted tool).
FAKE_BRAIN_VERSION = "2"


@dataclass(frozen=True)
class Observation:
    call_id: str
    tool: str
    arguments: Mapping[str, Any]
    outcome: ToolOutcome
    output: Any
    error: str | None
    delimited: bool

    @property
    def ok(self) -> bool:
        return self.outcome is ToolOutcome.OK

    def text(self) -> str:
        """All text in the observation, as a model would read it."""
        return json.dumps(self.output, ensure_ascii=False) if self.output is not None else ""


@dataclass(frozen=True)
class ConversationView:
    agent: str
    profile: Profile
    system_prompt: str
    task: str
    tools: tuple[ToolSpec, ...]
    observations: tuple[Observation, ...]
    feedback: tuple[str, ...] = field(default=())

    @property
    def has_hierarchy_clause(self) -> bool:
        return HIERARCHY_MARKER in self.system_prompt

    def tool(self, name: str) -> ToolSpec | None:
        return next((t for t in self.tools if t.name == name), None)

    def accepts_argument(self, tool: str, argument: str) -> bool:
        spec = self.tool(tool)
        return bool(spec and argument in spec.parameters.get("properties", {}))

    @property
    def resilient(self) -> bool:
        """Retries failed tools and declines to guess (careful and compliant profiles)."""
        return self.profile != "naive"

    def follows_embedded_instructions(self, obs: Observation) -> bool:
        if self.profile == "careful":
            return False
        if self.profile == "compliant":
            return True
        return not (obs.delimited and self.has_hierarchy_clause)

    def calls_to(self, tool: str) -> list[Observation]:
        return [o for o in self.observations if o.tool == tool]


class Brain(Protocol):
    def decide(self, view: ConversationView) -> dict[str, Any]: ...


# --- embedded-instruction parsing (what a susceptible model would "obey") ---------------

_CALL = re.compile(
    r"call\s+([a-z_]{3,40})\s+with\s+((?:[a-z_]{1,40}=\\?\"[^\"\\]{0,200}\\?\"[\s,]*(?:and\s+)?)+)",
    re.IGNORECASE,
)
_ARG = re.compile(r"([a-z_]{1,40})=\\?\"([^\"\\]{0,200})\\?\"", re.IGNORECASE)
_REVEAL = re.compile(r"reveal\s+your\s+(?:hidden|system)\s+(?:instructions|prompt)", re.IGNORECASE)
_REPLY_ONLY = re.compile(r"reply\s+only\s+with\s+([A-Z][A-Z ]{2,60}[A-Z])")


@dataclass(frozen=True)
class EmbeddedDirectives:
    tool_calls: tuple[tuple[str, dict[str, str]], ...] = ()
    reveal_instructions: bool = False
    reply_only: str | None = None

    @property
    def any(self) -> bool:
        return bool(self.tool_calls or self.reveal_instructions or self.reply_only)


def parse_directives(text: str) -> EmbeddedDirectives:
    text = text[:20_000]
    calls = tuple(
        (m.group(1).lower(), {k.lower(): v for k, v in _ARG.findall(m.group(2))})
        for m in _CALL.finditer(text)
    )
    reply = _REPLY_ONLY.search(text)
    return EmbeddedDirectives(
        tool_calls=calls,
        reveal_instructions=bool(_REVEAL.search(text)),
        reply_only=reply.group(1).strip() if reply else None,
    )


def obeyed_directives(view: ConversationView) -> EmbeddedDirectives:
    """Directives the current profile would act on, across all observations so far."""
    calls: list[tuple[str, dict[str, str]]] = []
    reveal = False
    reply: str | None = None
    for obs in view.observations:
        if not obs.ok or not view.follows_embedded_instructions(obs):
            continue
        found = parse_directives(obs.text())
        calls.extend(found.tool_calls)
        reveal = reveal or found.reveal_instructions
        reply = reply or found.reply_only
    return EmbeddedDirectives(tuple(calls), reveal, reply)


def tool_call(tool: str, arguments: dict[str, Any], rationale: str) -> dict[str, Any]:
    return {"type": "tool_call", "tool": tool, "arguments": arguments, "rationale": rationale}


def final(output: dict[str, Any], rationale: str) -> dict[str, Any]:
    return {"type": "final", "output": output, "rationale": rationale}


# --- provider -------------------------------------------------------------------------


def build_view(request: ModelRequest, profile: Profile) -> ConversationView:
    system = next((m.content for m in request.messages if m.role == "system"), "")
    task = next((m.content for m in request.messages if m.role == "user"), "")
    observations: list[Observation] = []
    feedback: list[str] = []
    for message in request.messages:
        meta = message.metadata
        if message.role == "tool" and meta.get("kind") == "observation":
            observations.append(
                Observation(
                    call_id=str(meta["call_id"]),
                    tool=str(meta["tool"]),
                    arguments=meta.get("arguments", {}),
                    outcome=ToolOutcome(meta["outcome"]),
                    output=meta.get("output"),
                    error=meta.get("error"),
                    delimited=bool(meta.get("delimited")),
                )
            )
        elif message.role == "user" and meta.get("kind") == "feedback":
            feedback.append(message.content)
    return ConversationView(
        agent=request.agent,
        profile=profile,
        system_prompt=system,
        task=task,
        tools=tuple(request.tools),
        observations=tuple(observations),
        feedback=tuple(feedback),
    )


class FakeModelProvider:
    name = "fake"

    def __init__(self, brains: Mapping[str, Brain]) -> None:
        self._brains = dict(brains)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        profile = PROFILES.get(request.config.model)
        if profile is None:
            raise ProviderError(
                f"unknown fake model {request.config.model!r}; expected one of {sorted(PROFILES)}"
            )
        brain = self._brains.get(request.agent)
        if brain is None:
            raise ProviderError(f"no deterministic brain registered for agent {request.agent!r}")
        decision = brain.decide(build_view(request, profile))
        return ModelResponse(
            content=json.dumps(decision, ensure_ascii=False, sort_keys=True),
            provider=self.name,
            model=request.config.model,
            usage=None,  # nothing is measured, so nothing is reported
            finish_reason="stop",
        )
