"""FastAPI dependencies."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from agentscope.services import Services


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)
