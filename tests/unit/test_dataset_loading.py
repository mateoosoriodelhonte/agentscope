"""Hardened dataset loading: limits, strictness, and the directory convention."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentscope.datasets.issues import DatasetError, Issue, json_pointer
from agentscope.datasets.loader import (
    MAX_DEPTH,
    MAX_DIR_JSON_FILES,
    MAX_FILE_BYTES,
    MAX_SAFE_INTEGER,
    classify,
    load_dataset_bytes,
    load_dataset_dir,
    load_dataset_file,
    load_gate_file,
)
from agentscope.domain.evaluation import CheckName


def document(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "demo",
        "agent": "research_assistant",
        "cases": [{"id": "c1", "task": "What opened in 1987?", "expected": {"value": "x"}}],
    }
    base.update(overrides)
    return base


def with_case(**fields: Any) -> dict[str, Any]:
    return document(cases=[{"id": "c1", "task": "What opened in 1987?", **fields}])


def encode(value: Any) -> bytes:
    return json.dumps(value).encode()


def issues_of(data: bytes) -> list[Issue]:
    with pytest.raises(DatasetError) as info:
        load_dataset_bytes(data)
    return list(info.value.issues)


def only_issue(data: bytes) -> Issue:
    issues = issues_of(data)
    assert len(issues) == 1, issues
    return issues[0]


def test_a_valid_document_loads() -> None:
    dataset = load_dataset_bytes(encode(document()))
    assert dataset.name == "demo"
    assert [c.id for c in dataset.cases] == ["c1"]


# --- size, encoding, syntax -------------------------------------------------------------


def test_input_at_the_size_limit_loads_and_one_byte_more_does_not() -> None:
    body = encode(document())
    at_limit = body + b" " * (MAX_FILE_BYTES - len(body))
    assert load_dataset_bytes(at_limit).name == "demo"
    assert "larger than" in only_issue(at_limit + b" ").message


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "big.dataset.json"
    with path.open("wb") as handle:
        handle.write(encode(document()))
        handle.truncate(4 * MAX_FILE_BYTES)  # sparse: cheap to create, too big to accept
    with pytest.raises(DatasetError) as info:
        load_dataset_file(path)
    assert "larger than" in info.value.issues[0].message
    assert info.value.issues[0].source == "big.dataset.json"


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe{}",  # UTF-16 byte-order mark
        json.dumps(
            document(description="caf\N{LATIN SMALL LETTER E WITH ACUTE}"), ensure_ascii=False
        ).encode("latin-1"),
        b'{"name": "\xc3"}',  # truncated multi-byte sequence
    ],
)
def test_only_utf8_is_accepted(data: bytes) -> None:
    assert "not valid UTF-8" in only_issue(data).message


@pytest.mark.parametrize("data", [b"", b"{", b'{"name": "demo",}', b"[1, 2"])
def test_syntax_errors_are_reported_with_a_position(data: bytes) -> None:
    issue = only_issue(data)
    assert issue.path == ""
    assert issue.message.startswith("invalid JSON")
    assert "line 1" in issue.message


def test_integer_literals_beyond_the_digit_limit_are_refused() -> None:
    data = b'{"name": "demo", "version": ' + b"9" * 5000 + b"}"
    assert only_issue(data).message.startswith("invalid JSON")


# --- ambiguous JSON ---------------------------------------------------------------------


def test_duplicate_keys_are_refused_with_their_location() -> None:
    data = (
        b'{"name": "demo", "cases": [{"id": "c1", "task": "t", "forbidden_tools": ["send_email"],'
        b' "forbidden_tools": [], "expected": {"value": "x"}}], "name": "other"}'
    )
    assert {(i.path, i.message) for i in issues_of(data)} == {
        ("/name", "duplicate key"),
        ("/cases/0/forbidden_tools", "duplicate key"),
    }


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "-1e400"])
def test_non_finite_numbers_are_refused(literal: str) -> None:
    data = (
        b'{"name": "demo", "cases": [{"id": "c1", "task": "t", "expected": '
        b'{"match": "numeric", "value": 1, "tolerance": ' + literal.encode() + b"}}]}"
    )
    issue = only_issue(data)
    assert issue.path == "/cases/0/expected/tolerance"
    assert "non-finite" in issue.message


@pytest.mark.parametrize("number", [2**53, -(2**53), 10**400])
def test_integers_beyond_the_i_json_range_are_refused(number: int) -> None:
    # Past 2**53 parsers that use doubles read another number; past float range numeric
    # matching cannot even convert it.
    data = (
        b'{"name": "demo", "cases": [{"id": "c1", "task": "t", "expected": '
        b'{"match": "numeric", "value": ' + str(number).encode() + b"}}]}"
    )
    issue = only_issue(data)
    assert issue.path == "/cases/0/expected/value"
    assert "not portable JSON" in issue.message


def test_the_largest_safe_integer_loads() -> None:
    dataset = load_dataset_bytes(
        encode(with_case(expected={"match": "numeric", "value": MAX_SAFE_INTEGER}))
    )
    assert dataset.cases[0].expected is not None
    assert dataset.cases[0].expected.value == MAX_SAFE_INTEGER


def test_issue_lists_stay_bounded_for_floods_of_duplicate_keys() -> None:
    flood = b",".join(b'"a": 1' for _ in range(10_000))
    issues = issues_of(b'{"name": "demo", "description": {' + flood + b'}, "cases": []}')
    assert 1 <= len(issues) <= 50
    assert {i.message for i in issues} == {"duplicate key"}


def test_unpaired_surrogates_are_refused_in_values_and_keys() -> None:
    value = only_issue(b'{"name": "demo", "description": "\\ud800", "cases": []}')
    assert (value.path, value.message) == ("/description", "string contains an unpaired surrogate")
    key = only_issue(b'{"name": "demo", "\\udfff": 1, "cases": []}')
    assert key.path == "/\\udfff"  # escaped so the pointer itself stays printable
    # A valid surrogate pair is an ordinary character.
    assert (
        load_dataset_bytes(encode(document(description="\U0001f600"))).description == "\U0001f600"
    )


def test_nesting_depth_is_bounded() -> None:
    def nested(levels: int) -> bytes:
        # root, cases, case and expected are 4 levels; the value adds the rest.
        value: Any = "x"
        for _ in range(levels - 4):
            value = [value]
        return encode(with_case(expected={"match": "exact", "value": value}))

    assert load_dataset_bytes(nested(MAX_DEPTH)).cases[0].expected is not None
    issue = only_issue(nested(MAX_DEPTH + 1))
    assert issue.path == "/cases/0/expected/value" + "/0" * (MAX_DEPTH - 4)
    assert "nested deeper" in issue.message


def test_pathological_nesting_is_an_error_not_a_crash() -> None:
    assert "nested deeper" in only_issue(b"[" * 200_000 + b"]" * 200_000).message


# --- strict validation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "path"),
    [
        ({"expected_tool_calls": [{"tool": "search_documents", "min_calls": "2"}]}, "/min_calls"),
        ({"expected": {"match": "numeric", "value": 1, "tolerance": "0.1"}}, "/tolerance"),
        ({"success_criteria": {"must_complete": 1}}, "/success_criteria/must_complete"),
        ({"faults": [{"tool": "search_documents", "kind": "error", "on_call": True}]}, "/on_call"),
    ],
)
def test_values_are_never_coerced(fields: dict[str, Any], path: str) -> None:
    issue = only_issue(encode(with_case(**fields)))
    assert issue.path.startswith("/cases/0/")
    assert issue.path.endswith(path)


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_schema_version_must_be_exactly_one(version: object) -> None:
    assert only_issue(encode(document(schema_version=version))).path == "/schema_version"


def test_validation_errors_point_at_the_field() -> None:
    issues = issues_of(
        encode(
            with_case(
                shell="rm -rf /",
                expected_tool_calls=[
                    {"tool": "search_documents", "arguments": {"Bad": {"contains": "x"}}}
                ],
            )
        )
    )
    paths = {i.path: i.message for i in issues}
    assert "/cases/0/shell" in paths
    assert paths["/cases/0/expected_tool_calls/0/arguments/Bad"].startswith("invalid key")


def test_model_level_rules_are_reported_without_pydantic_prefixes() -> None:
    issue = only_issue(encode(with_case(expected={"match": "normalized", "value": 5})))
    assert issue.path == "/cases/0/expected"
    assert issue.message == "match 'normalized' requires a string 'value'"


def test_a_top_level_array_is_not_a_dataset() -> None:
    assert only_issue(b"[]").path == ""


# --- files ------------------------------------------------------------------------------


def test_missing_file_and_directory_are_refused(tmp_path: Path) -> None:
    with pytest.raises(DatasetError) as missing:
        load_dataset_file(tmp_path / "absent.dataset.json")
    assert missing.value.issues[0].message.startswith("cannot open")
    with pytest.raises(DatasetError) as directory:
        load_dataset_file(tmp_path)
    assert directory.value.issues[0].message == "not a regular file"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_a_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.dataset.json"
    os.mkfifo(fifo)
    with pytest.raises(DatasetError) as info:
        load_dataset_file(fifo)
    assert info.value.issues[0].message == "not a regular file"


def test_file_issues_name_their_source(tmp_path: Path) -> None:
    path = tmp_path / "bad.dataset.json"
    path.write_bytes(encode(with_case(title=7)))
    with pytest.raises(DatasetError) as info:
        load_dataset_file(path)
    assert str(info.value.issues[0]).startswith("bad.dataset.json#/cases/0/title: ")


# --- directories ------------------------------------------------------------------------


def write(directory: Path, name: str, value: Any) -> Path:
    path = directory / name
    path.write_bytes(encode(value))
    return path


def two_case_files(directory: Path) -> None:
    write(directory, "b.dataset.json", document(cases=[{"id": "b1", "task": "t", "tags": []}]))
    write(directory, "a.dataset.json", document())


def test_classify_names() -> None:
    assert classify("qa.dataset.json") == "dataset"
    assert classify("gate.json") == "gate"
    assert classify("research_assistant@v2.baseline.json") == "baseline"
    assert classify(".dataset.json") is None
    assert classify("notes.json") is None


def test_dataset_files_merge_in_name_order_and_other_files_are_left_alone(
    tmp_path: Path,
) -> None:
    two_case_files(tmp_path)
    write(tmp_path, "gate.json", {"min_pass_rate": 0.5})
    write(tmp_path, "research_assistant@v2.baseline.json", {"anything": True})
    (tmp_path / "README.md").write_text("not JSON")
    (tmp_path / "nested").mkdir()
    write(tmp_path / "nested", "junk.json", [1, 2, 3])
    dataset = load_dataset_dir(tmp_path)
    assert [c.id for c in dataset.cases] == ["c1", "b1"]
    assert dataset.name == "demo"


def test_unrecognized_json_files_are_errors(tmp_path: Path) -> None:
    two_case_files(tmp_path)
    write(tmp_path, "extra-cases.json", document())
    with pytest.raises(DatasetError) as info:
        load_dataset_dir(tmp_path)
    assert info.value.issues[0].source == "extra-cases.json"


@pytest.mark.parametrize("name", ["more.dataset.JSON", "Gate.json", "cases.Json"])
def test_json_files_are_recognised_whatever_the_case_of_their_extension(
    tmp_path: Path, name: str
) -> None:
    # Only the exact lower-case names are accepted; anything else that is JSON is reported
    # instead of being silently left out of the suite.
    two_case_files(tmp_path)
    write(tmp_path, name, document(cases=[{"id": "x1", "task": "t"}]))
    with pytest.raises(DatasetError) as info:
        load_dataset_dir(tmp_path)
    assert [i.source for i in info.value.issues] == [name]
    assert "unrecognized JSON file" in info.value.issues[0].message


def test_dataset_files_must_share_a_header(tmp_path: Path) -> None:
    write(tmp_path, "a.dataset.json", document())
    write(tmp_path, "b.dataset.json", document(name="other", cases=[{"id": "b1", "task": "t"}]))
    with pytest.raises(DatasetError) as info:
        load_dataset_dir(tmp_path)
    assert [(i.source, i.path) for i in info.value.issues] == [("b.dataset.json", "/name")]


def test_case_ids_must_be_unique_across_files(tmp_path: Path) -> None:
    write(tmp_path, "a.dataset.json", document())
    write(tmp_path, "b.dataset.json", document())
    with pytest.raises(DatasetError) as info:
        load_dataset_dir(tmp_path)
    issue = info.value.issues[0]
    assert (issue.source, issue.path) == ("b.dataset.json", "/cases/0/id")
    assert "also in a.dataset.json" in issue.message


def test_errors_from_several_files_are_collected(tmp_path: Path) -> None:
    write(tmp_path, "a.dataset.json", with_case(title=1))
    write(tmp_path, "b.dataset.json", with_case(task=""))
    with pytest.raises(DatasetError) as info:
        load_dataset_dir(tmp_path)
    assert {i.source for i in info.value.issues} == {"a.dataset.json", "b.dataset.json"}


def test_a_directory_without_datasets_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "gate.json", {})
    with pytest.raises(DatasetError, match=r"no \*\.dataset\.json files"):
        load_dataset_dir(tmp_path)


def test_a_file_is_not_a_dataset_directory(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="not a directory"):
        load_dataset_dir(write(tmp_path, "a.dataset.json", document()))


def test_too_many_json_files_are_refused(tmp_path: Path) -> None:
    for index in range(MAX_DIR_JSON_FILES + 1):
        (tmp_path / f"{index:03}.baseline.json").write_text("{}")
    with pytest.raises(DatasetError, match=f"more than {MAX_DIR_JSON_FILES} JSON files"):
        load_dataset_dir(tmp_path)


def test_symlinks_that_escape_the_directory_are_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    outside = write(tmp_path, "outside.json", document(cases=[{"id": "secret", "task": "t"}]))
    inside = tmp_path / "suite"
    inside.mkdir()
    write(inside, "a.dataset.json", document())
    (inside / "b.dataset.json").symlink_to(inside / "a.dataset.json")  # stays inside: followed
    (inside / "z.dataset.json").symlink_to(outside)
    with pytest.raises(DatasetError, match="duplicate case id 'c1'"):
        load_dataset_dir(inside)  # b is read (and duplicates a); z is never read
    (inside / "b.dataset.json").unlink()
    assert [c.id for c in load_dataset_dir(inside).cases] == ["c1"]
    assert "z.dataset.json" in caplog.text


# --- gate files and errors ----------------------------------------------------------------


def test_gate_files_are_strictly_validated(tmp_path: Path) -> None:
    gate = load_gate_file(write(tmp_path, "gate.json", {"required_checks": ["tool_order"]}))
    assert gate.required_checks == [CheckName.TOOL_ORDER]
    with pytest.raises(DatasetError) as info:
        load_gate_file(write(tmp_path, "gate.json", {"min_pass_rate": "0.9"}))
    issue = info.value.issues[0]
    assert (issue.source, issue.path) == ("gate.json", "/min_pass_rate")


def test_dataset_error_needs_issues_and_summarises_them() -> None:
    with pytest.raises(ValueError, match="at least one issue"):
        DatasetError([])
    error = DatasetError([Issue(f"/cases/{i}", "bad") for i in range(5)])
    assert str(error) == "/cases/0: bad; /cases/1: bad; /cases/2: bad; and 2 more"
    assert str(Issue("", "empty")) == "(document): empty"


def test_json_pointer_escapes_reserved_characters() -> None:
    assert json_pointer(["a/b", "c~d", 0]) == "/a~1b/c~0d/0"
    assert json_pointer([]) == ""
