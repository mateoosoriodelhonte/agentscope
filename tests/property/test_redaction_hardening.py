"""Redaction edge cases: linear time on hostile input, configured values that are ordinary
words, secrets in keys and key names, and characters that cannot be stored."""

from __future__ import annotations

import dataclasses
import json
import logging
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agentscope.config import Settings
from agentscope.datasets.packaged import load_builtin_dataset
from agentscope.services import Services, build_redactor
from agentscope.tracing.redaction import REDACTED, Redactor, sanitize_text

KIB = 1024

# Each input targets one pattern's worst case at 64 KiB (the design's budget is 50 ms per
# pattern; the assertion leaves room for slow CI machines, a quadratic pattern takes seconds).
ADVERSARIAL = {
    "jwt-dash-runs": "eyJ-" * (16 * KIB),
    "jwt-short-segments": ("eyJ" + "a" * 8 + ".") * (5 * KIB),
    "private-key-begins": "-----BEGIN PRIVATE KEY-----" * 2400,
    "private-key-no-end": "-----BEGIN RSA PRIVATE KEY-----" + "A" * (64 * KIB),
    "bearer-spaces": "Bearer " + " " * (64 * KIB),
    "assignment-names": "password_" * (7 * KIB),
    "assignment-spaces": "password" + " " * (64 * KIB) + "x",
    "assignment-separators": "a=" * (32 * KIB),
    "url-schemes": "a://" * (16 * KIB),
    "url-userinfo": "http://" + "u:" * (32 * KIB),
    "url-no-at": "http://user:" + "p" * (64 * KIB),
    "openai-prefixes": "sk-" * (21 * KIB),
    "github-prefixes": "ghp_" * (16 * KIB),
    "aws-prefixes": "AKIA" * (16 * KIB),
    "slack-prefixes": "xoxb-" * (13 * KIB),
    "configured-near-misses": "Pg-pass" * (9 * KIB),
}


@pytest.mark.parametrize("text", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
def test_every_pattern_runs_in_linear_time(text: str) -> None:
    redactor = Redactor(secret_values=["Pg-pass9", "correct-horse-battery-staple-42"])
    started = time.perf_counter()
    redactor.redact_text(text)
    assert time.perf_counter() - started < 0.25


def test_a_large_hostile_dataset_value_is_redacted_in_seconds_not_hours() -> None:
    # The reported case: a 5 MB `eyJ-eyJ-...` value took about two hours.
    started = time.perf_counter()
    Redactor().redact({"expected": {"value": "eyJ-" * 1_300_000}})
    assert time.perf_counter() - started < 5.0


def test_jwts_are_still_redacted() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    assert Redactor().redact_text(f"token={jwt}") == "token=[REDACTED:assignment]"
    assert Redactor().redact_text(f"see {jwt} here") == "see [REDACTED:jwt] here"


def test_private_key_blocks_are_still_redacted() -> None:
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    text = f"-----BEGIN PRIVATE KEY----- stray\n{block} after"
    assert Redactor().redact_text(text) == (
        "-----BEGIN PRIVATE KEY----- stray\n[REDACTED:private_key] after"
    )


# --- configured values that are ordinary words (F12) ---------------------------------------


@pytest.mark.parametrize("weak", ["agentscope", "postgres", "12345678", "password", "aaaaaaaaaaaa"])
def test_weak_configured_values_never_rewrite_ordinary_text(weak: str) -> None:
    redactor = Redactor(secret_values=[weak])
    for text in ("agentscope.fake", "Which agentscope release added tool faults?", weak):
        assert redactor.redact_text(text) == text


def test_strong_short_configured_values_are_redacted_as_whole_tokens() -> None:
    redactor = Redactor(secret_values=["Pg-pass9"])
    assert redactor.redact_text("dsn password Pg-pass9.") == (
        "dsn password [REDACTED:configured_secret]."
    )
    assert redactor.redact_text("xPg-pass9x and Pg-pass99") == "xPg-pass9x and Pg-pass99"


def test_long_configured_values_are_redacted_wherever_they_appear() -> None:
    redactor = Redactor(secret_values=["correct-horse-battery-staple-42"])
    escaped = json.dumps("line\ncorrect-horse-battery-staple-42")  # "...\\ncorrect-..."
    assert "correct-horse" not in redactor.redact_text(escaped)


def test_url_credentials_are_redacted_even_when_the_password_is_weak() -> None:
    text = "could not connect to postgresql://agentscope:agentscope@db:5432/agentscope"
    assert Redactor(secret_values=["agentscope"]).redact_text(text) == (
        "could not connect to postgresql://agentscope:[REDACTED:url_password]@db:5432/agentscope"
    )


def test_provider_labels_survive_a_dsn_whose_password_is_the_product_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(database_url="postgresql://agentscope:agentscope@localhost/agentscope")
    with caplog.at_level(logging.WARNING, logger="agentscope.services"):
        redactor = build_redactor(settings)
    payload = {"provider": "agentscope.fake", "task": "Which agentscope release added faults?"}
    assert redactor.redact(payload) == payload
    assert "too short or too simple" in caplog.text
    assert "agentscope:agentscope" not in caplog.text


def test_datasets_mentioning_a_weak_configured_password_are_not_refused(
    services: Services,
) -> None:
    settings = Settings(database_url="postgresql://agentscope:agentscope@localhost/agentscope")
    strict = dataclasses.replace(services, redactor=build_redactor(settings))
    dataset = load_builtin_dataset("research-assistant")
    case = dataset.cases[0].model_copy(update={"task": "Which agentscope release added faults?"})
    dataset = dataset.model_copy(update={"cases": [case, *dataset.cases[1:]]})
    refusals = [i for i in strict.validate_dataset(dataset) if "looks like a secret" in i.message]
    assert refusals == []


# --- secrets in keys and under secret-like key names (F13) ---------------------------------


def test_secrets_used_as_object_keys_are_redacted_and_keys_stay_distinct() -> None:
    first = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234"
    second = "sk-proj-ZyXwVuTsRqPoNmLkJiHgFeDc9876"
    configured = "correct-horse-battery-staple-42"
    redactor = Redactor(secret_values=[configured])
    out = redactor.redact({first: 1, second: 2, configured: {"nested": 3}, "plain": 4})
    dumped = json.dumps(out)
    for secret in (first, second, configured):
        assert secret not in dumped
    assert out == {
        "[REDACTED:openai_key]": 1,
        "[REDACTED:openai_key]#2": 2,
        "[REDACTED:configured_secret]": {"nested": 3},
        "plain": 4,
    }
    assert redactor.redact(out) == out


def test_a_renamed_key_never_overwrites_an_existing_one() -> None:
    out = Redactor().redact({"[REDACTED:openai_key]": "kept", "sk-ant-api03-ZyXwVuTsRqPoNmLk": 1})
    assert out == {"[REDACTED:openai_key]": "kept", "[REDACTED:anthropic_key]": 1}
    out = Redactor().redact({"sk-proj-AbCdEfGhIjKlMnOpQrSt": 1, "[REDACTED:openai_key]": "kept"})
    assert out == {"[REDACTED:openai_key]#2": 1, "[REDACTED:openai_key]": "kept"}


@pytest.mark.parametrize(
    "key",
    [
        "SECRET_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "githubToken",
        "db_password_plain",
        "x-api-key",
        "ApiKey",
        "privateKey",
        "sessionId",
        "Set-Cookie",
        "clientSecret",
        "refresh_token",
        "Proxy-Authorization",
        "aws.credentials",
        "user passphrase",
        # Run together, as the earlier key pattern also caught them.
        "accesstoken",
        "X_AUTHTOKEN",
        "refreshtoken",
        "idtoken",
        "CLIENTSECRET",
    ],
)
def test_secret_like_key_names_are_redacted_wherever_the_term_appears(key: str) -> None:
    assert Redactor().redact({key: "value-1234"}) == {key: REDACTED}


@pytest.mark.parametrize(
    "key",
    [
        "input_tokens",
        "max_tokens",
        "gen_ai.usage.output_tokens",
        "token_count",
        "keywords",
        "primary_key",
        "monkey",
        "tokenizer",
        "secretary",
        "author",
        "passwordless_login",
    ],
)
def test_ordinary_key_names_are_kept(key: str) -> None:
    assert Redactor().redact({key: "value-1234"}) == {key: "value-1234"}


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
        ("SECRET_KEY = 'django-insecure-abc123'", "django-insecure"),
        ('export DB_PASSWORD="hunter2hunter2"', "hunter2"),
        ('{"githubToken": "abcd1234efgh"}', "abcd1234efgh"),
        ("password: hunter22", "hunter22"),
    ],
)
def test_assignments_to_secret_like_names_are_redacted(text: str, value: str) -> None:
    out = Redactor().redact_text(text)
    assert value not in out
    assert "[REDACTED:assignment]" in out


@pytest.mark.parametrize("text", ["max_tokens=1024", "input_tokens: 12", "note: hello world"])
def test_assignments_to_ordinary_names_are_kept(text: str) -> None:
    assert Redactor().redact_text(text) == text


# --- characters storage cannot hold (F23) --------------------------------------------------


def test_nul_and_lone_surrogates_are_replaced_everywhere() -> None:
    out = Redactor().redact({"a\x00": "x\ud83dy\x00", "list": ["\udc00", "\ud83d\ude00"]})
    assert out == {"a\ufffd": "x\ufffdy\ufffd", "list": ["\ufffd", "\U0001f600"]}
    json.dumps(out, ensure_ascii=False).encode("utf-8")  # strict UTF-8 accepts it


def test_sanitize_text_leaves_ordinary_text_alone() -> None:
    text = "plain ascii, ünïcödé and 😀"
    assert sanitize_text(text) is text


unicode_text = st.text(
    alphabet=st.characters(codec=None, exclude_categories=()),  # surrogates and NUL included
    max_size=20,
)


@given(
    tree=st.recursive(
        st.one_of(st.none(), st.integers(), unicode_text),
        lambda inner: (
            st.lists(inner, max_size=3) | st.dictionaries(unicode_text, inner, max_size=3)
        ),
        max_leaves=10,
    )
)
@settings(max_examples=200, deadline=None)
def test_redacted_output_is_always_storable_and_stable(tree: object) -> None:
    redactor = Redactor(secret_values=["correct-horse-battery-staple-42"])
    once = redactor.redact({"x": tree})
    json.dumps(once, ensure_ascii=False).encode("utf-8")
    assert "\x00" not in json.dumps(once, ensure_ascii=False)
    assert redactor.redact(once) == once
