from __future__ import annotations

import pytest

from agentscope.agents.base import AgentCatalog
from agentscope.runtime.runner import AgentRunner
from agentscope.suite_runner import SuiteRunner


@pytest.fixture
def suite_runner(runner: AgentRunner, catalog: AgentCatalog) -> SuiteRunner:
    return SuiteRunner(runner, catalog)
