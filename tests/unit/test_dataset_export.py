"""Canonical export and safe export paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentscope.datasets.io import export_dataset, export_filename, export_path
from agentscope.datasets.loader import classify, load_dataset_bytes
from agentscope.domain.datasets import ArgMatcher, Dataset, EvalCase, ExpectedToolCall


def sample() -> Dataset:
    return Dataset(
        name="demo",
        version="2026.1",
        agent="research_assistant",
        cases=[
            EvalCase(
                id="c1",
                task="Find the bridge.",
                expected_tool_calls=[
                    ExpectedToolCall(
                        tool="search_documents",
                        arguments={
                            "query": ArgMatcher(contains="bridge"),
                            "top_k": ArgMatcher(one_of=[3, 5]),
                            "include_unverified": ArgMatcher(equals=None),
                        },
                    )
                ],
            )
        ],
    )


def test_export_is_canonical_pretty_json() -> None:
    data = export_dataset(sample())
    text = data.decode("utf-8")
    assert text.endswith("}\n")
    assert text.startswith('{\n  "agent": "research_assistant",\n  "cases": [')
    document = json.loads(text)
    assert list(document) == sorted(document)
    assert document["cases"][0]["success_criteria"]["must_complete"] is True  # defaults kept


def test_equal_datasets_export_identical_bytes() -> None:
    explicit = Dataset.model_validate_json(
        '{"cases": [{"task": "Find the bridge.", "id": "c1", "title": "", "tags": [],'
        ' "expected_tool_calls": [{"tool": "search_documents", "min_calls": 1, "arguments":'
        ' {"include_unverified": {"equals": null}, "top_k": {"one_of": [3, 5]},'
        ' "query": {"contains": "bridge"}}}]}],'
        ' "version": "2026.1", "agent": "research_assistant", "name": "demo", "schema_version": 1}'
    )
    assert explicit == sample()
    assert export_dataset(explicit) == export_dataset(sample())


def test_each_argument_matcher_keeps_only_its_comparison() -> None:
    document = json.loads(export_dataset(sample()))
    assert document["cases"][0]["expected_tool_calls"][0]["arguments"] == {
        "include_unverified": {"equals": None},
        "query": {"contains": "bridge"},
        "top_k": {"one_of": [3, 5]},
    }


def test_export_round_trips() -> None:
    data = export_dataset(sample())
    loaded = load_dataset_bytes(data)
    assert loaded == sample()
    assert loaded.content_hash() == sample().content_hash()
    assert export_dataset(loaded) == data


def test_export_file_names_follow_the_dataset_convention() -> None:
    name = export_filename(sample())
    assert name == "demo@2026.1.dataset.json"
    assert classify(name) == "dataset"


def test_export_paths_stay_inside_the_output_directory(tmp_path: Path) -> None:
    assert export_path(tmp_path, sample()) == tmp_path.resolve() / "demo@2026.1.dataset.json"
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    out = tmp_path / "out"
    out.mkdir()
    (out / "demo@2026.1.dataset.json").symlink_to(outside)
    with pytest.raises(ValueError, match="resolves outside"):
        export_path(out, sample())


@pytest.mark.parametrize("name", ["../escape", "a/b", ".hidden", ""])
def test_unvalidated_names_never_become_paths(tmp_path: Path, name: str) -> None:
    forged = sample().model_copy(update={"name": name})  # bypasses validation
    with pytest.raises(ValueError, match="String should match pattern"):
        export_path(tmp_path, forged)
