"""Google ADK control loop for the MCP-backed Pokemon Red agent."""

from typing import Any


__all__ = ["app", "root_agent"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from pokemon_agent.adk_agent.web.app import app, root_agent

    return {"app": app, "root_agent": root_agent}[name]
