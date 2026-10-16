import agentscope


def test_version_is_exposed() -> None:
    assert isinstance(agentscope.__version__, str)
    assert agentscope.__version__
