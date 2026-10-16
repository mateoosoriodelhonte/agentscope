import json

from hypothesis import given, settings
from hypothesis import strategies as st

from agentscope.tracing.redaction import Redactor

SECRETS = [
    "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx1234",
    "sk-ant-api03-ZyXwVuTsRqPoNmLkJiHg",
    "ghp_0123456789abcdefghijABCDEFGHIJ",
    "AKIAIOSFODNN7EXAMPLE",
    "xoxb-1234567890-abcdefghij",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
]
json_leaf = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=30))
safe_keys = st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=8)


@given(
    secret=st.sampled_from(SECRETS),
    prefix=st.text(max_size=20),
    suffix=st.text(alphabet=" .,;\n", max_size=5),
    tree=st.recursive(
        json_leaf,
        lambda inner: st.lists(inner, max_size=3) | st.dictionaries(safe_keys, inner, max_size=3),
        max_leaves=8,
    ),
)
@settings(max_examples=200, deadline=None)
def test_secret_shaped_values_never_survive(
    secret: str, prefix: str, suffix: str, tree: object
) -> None:
    payload = {"data": tree, "note": f"{prefix} {secret}{suffix}", "nested": [{"value": secret}]}
    out = json.dumps(Redactor().redact(payload))
    assert secret not in out


@given(
    key=st.sampled_from(
        [
            "api_key",
            "Authorization",
            "x-api-key",
            "password",
            "client_secret",
            "access_token",
            "session_id",
            "OPENAI_API_KEY",
        ]
    ),
    value=st.text(min_size=1, max_size=40),
)
def test_sensitive_keys_are_redacted_whole(key: str, value: str) -> None:
    assert Redactor().redact({key: value})[key] == "[REDACTED]"


def test_usage_fields_are_not_mistaken_for_tokens() -> None:
    data = {"gen_ai.usage.input_tokens": 12, "output_tokens": 3, "max_tokens": 5}
    assert Redactor().redact(data) == data


def test_configured_secret_values_are_redacted_anywhere() -> None:
    redactor = Redactor(secret_values=["local-key-ABCDEFGH"])
    assert "local-key-ABCDEFGH" not in redactor.redact_text("auth with local-key-ABCDEFGH please")


@given(
    tree=st.recursive(
        json_leaf,
        lambda inner: st.lists(inner, max_size=3) | st.dictionaries(safe_keys, inner, max_size=3),
        max_leaves=10,
    )
)
def test_redaction_is_idempotent(tree: object) -> None:
    redactor = Redactor()
    once = redactor.redact({"x": tree, "token": "abc", "s": "Bearer abcdefghijklmnopqrstuvwxyz"})
    assert redactor.redact(once) == once
