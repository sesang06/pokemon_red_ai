"""Optional real-time web debugger for the Pokemon Red runtime."""

from pokemon_agent.dashboard.events import LiveEventHub
from pokemon_agent.dashboard.server import DashboardService

__all__ = ["DashboardService", "LiveEventHub"]
