"""Properties of the hardened loader and the canonical export."""

from __future__ import annotations

from importlib import resources
from typing import get_args

from hypothesis import given, settings
from hypothesis import strategies as st

from agentscope.datasets.io import export_dataset
from agentscope.datasets.issues import DatasetError
from agentscope.datasets.loader import load_dataset_bytes
from agentscope.domain.datasets import (
    ArgMatcher,
    Dataset,
    EvalCase,
    ExpectedAnswer,
    ExpectedToolCall,
    LimitsOverride,
    MatchMode,
    OutputStatus,
    SuccessCriteria,
)
from agentscope.domain.tools import FaultKind, FaultSpec, ModelFaultKind, ModelFaultSpec

SEED = resources.files("agentscope.datasets.builtin").joinpath("research_assistant.json")


def loads_or_rejects(data: bytes) -> None:
    """The only acceptable outcomes: a Dataset, or a DatasetError that says why."""
    result: Dataset | DatasetError
    try:
        result = load_dataset_bytes(data)
    except DatasetError as exc:
        result = exc
    if isinstance(result, DatasetError):
        assert result.issues
        for issue in result.issues:
            str(issue).encode("utf-8")  # always printable, even for hostile input
    else:
        assert isinstance(result, Dataset)
        result.content_hash()  # and always hashable


@given(st.binary(max_size=4096))
@settings(max_examples=300, deadline=1000)
def test_arbitrary_bytes_give_a_dataset_or_a_dataset_error(data: bytes) -> None:
    loads_or_rejects(data)


@given(
    edits=st.lists(
        st.tuples(st.integers(0, 12_000), st.integers(0, 12), st.binary(max_size=12)),
        min_size=1,
        max_size=4,
    )
)
@settings(max_examples=300, deadline=1000)
def test_mutated_datasets_give_a_dataset_or_a_dataset_error(
    edits: list[tuple[int, int, bytes]],
) -> None:
    data = bytearray(SEED.read_bytes())
    for position, length, replacement in edits:
        data[position : position + length] = replacement
    loads_or_rejects(bytes(data))


# --- generated datasets ---------------------------------------------------------------

slugs = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,15}", fullmatch=True)
identifiers = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)
texts = st.text(max_size=40)
STORABLE = st.characters(blacklist_characters="\x00", blacklist_categories=("Cs",))
finite = st.floats(allow_nan=False, allow_infinity=False)
numbers = st.integers(-(10**12), 10**12) | finite
json_values = st.recursive(
    st.none() | st.booleans() | numbers | st.text(max_size=12),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=6), inner, max_size=3)
    ),
    max_leaves=6,
)
optional_count = st.none() | st.integers(1, 50)
fields = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}(\.[A-Za-z0-9_]{1,5}){0,2}", fullmatch=True)

answers = st.one_of(
    st.builds(ExpectedAnswer, field=fields, match=st.just(MatchMode.EXACT), value=json_values),
    st.builds(ExpectedAnswer, field=fields, match=st.just(MatchMode.NORMALIZED), value=texts),
    st.builds(
        ExpectedAnswer,
        field=fields,
        match=st.just(MatchMode.CONTAINS),
        value=texts,
        max_chars=st.none() | st.integers(1, 5000),
    ),
    st.builds(
        ExpectedAnswer,
        field=fields,
        match=st.just(MatchMode.NUMERIC),
        value=numbers,
        tolerance=st.none() | st.floats(min_value=0, allow_infinity=False),
    ),
    st.builds(
        ExpectedAnswer,
        field=fields,
        match=st.just(MatchMode.ONE_OF),
        values=st.lists(json_values, min_size=1, max_size=4),
    ),
)

matchers = st.one_of(
    st.builds(ArgMatcher, equals=json_values),
    st.builds(ArgMatcher, contains=st.text(min_size=1, max_size=20)),
    st.builds(ArgMatcher, one_of=st.lists(json_values, min_size=1, max_size=3)),
)


@st.composite
def expected_calls(draw: st.DrawFn) -> ExpectedToolCall:
    min_calls = draw(st.integers(1, 5))
    return ExpectedToolCall(
        tool=draw(identifiers),
        arguments=draw(st.dictionaries(identifiers, matchers, max_size=3)),
        min_calls=min_calls,
        max_calls=draw(st.none() | st.integers(min_calls, 50)),
    )


faults = st.builds(
    FaultSpec,
    tool=identifiers,
    kind=st.sampled_from(FaultKind),
    on_call=optional_count,
    times=optional_count,
    payload=st.none() | st.text(min_size=1, max_size=30),
)
model_faults = st.builds(
    ModelFaultSpec,
    kind=st.sampled_from(ModelFaultKind),
    on_call=st.integers(1, 50),
    times=st.integers(1, 50),
)
statuses = st.sampled_from(get_args(OutputStatus))
criteria = st.builds(
    SuccessCriteria,
    must_complete=st.booleans(),
    expected_output_status=st.none() | statuses,
    expect_recovery=st.none() | st.booleans(),
    max_steps_budget=optional_count,
    max_tool_calls=st.none() | st.integers(0, 100),
)
limits = st.builds(
    LimitsOverride,
    max_steps=optional_count,
    tool_timeout_s=st.none() | st.floats(min_value=0, max_value=30, exclude_min=True),
    run_timeout_s=st.none() | st.floats(min_value=0, max_value=600, exclude_min=True),
)


@st.composite
def cases(draw: st.DrawFn) -> EvalCase:
    permitted = draw(st.none() | st.lists(identifiers, max_size=3))
    forbidden = [t for t in draw(st.lists(identifiers, max_size=3)) if t not in (permitted or [])]
    return EvalCase(
        id=draw(slugs),
        # Tasks and titles are StorableText: NUL is refused at the boundary by design.
        title=draw(st.text(alphabet=STORABLE, max_size=20)),
        task=draw(st.text(alphabet=STORABLE, min_size=1, max_size=60)),
        agent=draw(st.none() | identifiers),
        expected=draw(st.none() | answers),
        permitted_tools=permitted,
        expected_tool_calls=draw(st.lists(expected_calls(), max_size=2)),
        tool_order=draw(st.lists(identifiers, max_size=3)),
        tool_order_strict=draw(st.booleans()),
        forbidden_tools=forbidden,
        forbidden_output_substrings=draw(st.lists(st.text(min_size=1, max_size=20), max_size=3)),
        faults=draw(st.lists(faults, max_size=2)),
        model_faults=draw(st.lists(model_faults, max_size=2)),
        success_criteria=draw(criteria),
        limits=draw(st.none() | limits),
        tags=draw(st.lists(slugs, max_size=3)),
    )


datasets = st.builds(
    Dataset,
    name=slugs,
    version=slugs,
    description=st.text(max_size=40),
    agent=st.none() | identifiers,
    cases=st.lists(cases(), min_size=1, max_size=3, unique_by=lambda c: c.id),
)


@given(datasets)
@settings(max_examples=150, deadline=None)
def test_export_then_load_round_trips(dataset: Dataset) -> None:
    data = export_dataset(dataset)
    loaded = load_dataset_bytes(data)
    assert loaded == dataset
    assert loaded.content_hash() == dataset.content_hash()
    assert export_dataset(loaded) == data
