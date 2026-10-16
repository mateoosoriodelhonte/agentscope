"""Simulated tools and agents cannot reach the network, a shell, or the host filesystem.

An AST scan of every module under agentscope/tools and agentscope/agents bans the imports
and calls that would allow it. Packaged data stays readable through importlib.resources.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agentscope

PACKAGE = Path(agentscope.__file__).parent
SCANNED = sorted(
    path for folder in ("tools", "agents") for path in (PACKAGE / folder).rglob("*.py")
)

# Modules that reach the network, spawn processes, touch the filesystem, or load code.
BANNED_MODULES = frozenset(
    {
        "aiohttp",
        "builtins",
        "ctypes",
        "http",
        "httpx",
        "importlib.machinery",
        "importlib.util",
        "multiprocessing",
        "os",
        "pathlib",
        "pty",
        "requests",
        "runpy",
        "shutil",
        "socket",
        "ssl",
        "subprocess",
        "urllib",
        "urllib3",
    }
)
# Dotted names that do the same from otherwise harmless modules.
BANNED_NAMES = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "asyncio.open_connection",
        "codecs.open",
        "importlib.import_module",
        "io.open",
        "os.system",
    }
)
BANNED_BUILTINS = frozenset({"__import__", "compile", "eval", "exec", "open"})


def _banned_module(name: str) -> bool:
    return any(name == banned or name.startswith(f"{banned}.") for banned in BANNED_MODULES)


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def violations(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _banned_module(alias.name):
                    found.append(f"line {node.lineno}: import {alias.name}")
                local = alias.asname or alias.name.split(".")[0]
                aliases[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                dotted = f"{module}.{alias.name}"
                if _banned_module(module) or _banned_module(dotted) or dotted in BANNED_NAMES:
                    found.append(f"line {node.lineno}: from {module} import {alias.name}")
                aliases[alias.asname or alias.name] = dotted
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func, aliases)
            if name in BANNED_BUILTINS or name in BANNED_NAMES:
                found.append(f"line {node.lineno}: call {name}()")
    return found


def test_scan_covers_the_tools_and_agents() -> None:
    names = {path.name for path in SCANNED}
    assert {"calculator.py", "mock_api.py", "records.py", "research.py", "executor.py"} <= names


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_module_has_no_dangerous_imports_or_calls(path: Path) -> None:
    assert violations(path.read_text("utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "import httpx",
        "import requests",
        "import urllib.request",
        "from urllib import request",
        "import socket",
        "import subprocess",
        "from subprocess import run",
        "import shutil",
        "import ctypes",
        "import os",
        "from os import system",
        "import importlib\nimportlib.import_module('x')",
        "from importlib import import_module",
        "from importlib import import_module as load\nload('x')",
        "import importlib.util",
        "eval('1')",
        "exec('x = 1')",
        "compile('1', 'f', 'eval')",
        "open('/etc/passwd')",
        "__import__('os')",
        "import io\nio.open('f')",
        "import asyncio\nasyncio.create_subprocess_exec('ls')",
        "from pathlib import Path",
    ],
)
def test_scanner_flags_banned_patterns(source: str) -> None:
    assert violations(source), source


def test_scanner_allows_packaged_resources() -> None:
    source = (
        "from importlib import resources\n"
        "import posixpath\n"
        "raw = resources.files('agentscope.tools.data').joinpath('x.json').read_text('utf-8')\n"
        "path = posixpath.normpath('/a//b')\n"
    )
    assert violations(source) == []
