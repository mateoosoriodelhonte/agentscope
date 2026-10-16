"""AgentScope — understand, test, and improve AI agents."""

from importlib.metadata import PackageNotFoundError, version

from agentscope import _bootstrap  # noqa: F401  (must run before third-party imports)

try:
    __version__ = version("agentscope")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled checkout
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
