import pytest
from pydantic import ValidationError

from agentscope.domain.agents import AgentConfig, RetryPolicy
from agentscope.domain.common import canonical_json, content_hash
from agentscope.domain.datasets import ArgMatcher, Dataset, EvalCase, ExpectedAnswer, MatchMode


def _config(**overrides: object) -> AgentConfig:
    base: dict[str, object] = {
        "agent": "research_assistant",
        "version": "v1",
        "prompt_version": "p1",
        "tools": ["search_documents"],
    }
    base.update(overrides)
    return AgentConfig.model_validate(base)


def test_config_hash_ignores_label_but_not_behaviour() -> None:
    a = _config(label="first")
    b = _config(label="renamed")
    c = _config(retry=RetryPolicy(max_tool_retries=1))
    assert a.config_hash() == b.config_hash()
    assert a.config_hash() != c.config_hash()
    assert a.ref == "research_assistant@v1"


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({**_config().model_dump(), "shell": "rm -rf /"})


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})
    assert content_hash({"b": 1, "a": 2}).startswith("sha256:")


def test_expected_answer_consistency() -> None:
    ExpectedAnswer(match=MatchMode.NUMERIC, value=3.5, tolerance=0.1)
    with pytest.raises(ValidationError):
        ExpectedAnswer(match=MatchMode.NUMERIC, value="three")
    with pytest.raises(ValidationError):
        ExpectedAnswer(match=MatchMode.ONE_OF, value="x")
    with pytest.raises(ValidationError):
        ExpectedAnswer(match=MatchMode.EXACT, value="x", tolerance=1)


def test_arg_matcher_requires_exactly_one_comparison() -> None:
    assert ArgMatcher(equals=None).model_fields_set == {"equals"}
    ArgMatcher(contains="bridge")
    with pytest.raises(ValidationError):
        ArgMatcher()
    with pytest.raises(ValidationError):
        ArgMatcher(contains="a", one_of=["b"])


def test_dataset_rejects_duplicate_ids_and_conflicting_tools() -> None:
    case = EvalCase(id="c1", task="t")
    with pytest.raises(ValidationError):
        Dataset(name="d", cases=[case, case])
    with pytest.raises(ValidationError):
        EvalCase(id="c2", task="t", permitted_tools=["send_email"], forbidden_tools=["send_email"])
    dataset = Dataset(name="d", cases=[case])
    assert (
        dataset.content_hash()
        == Dataset(name="d", cases=[EvalCase(id="c1", task="t")]).content_hash()
    )
