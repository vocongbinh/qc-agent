from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StubProvider(Protocol):
    """Protocol for downstream stub management (e.g. WireMock, mock servers)."""

    def start(self) -> dict[str, str]:
        """Start stubs server, load initial stubs, return env vars (e.g. INVENTORY_BASE_URL)."""
        ...

    def load_scenario(self, scenario_name: str) -> None:
        """Switch or activate a specific scenario (e.g. 'inventory_ok', 'stock_404')."""
        ...

    def reset(self) -> None:
        """Reset stubs/scenarios to clean initial state."""
        ...

    def stop(self) -> None:
        """Stop and tear down the stubs server / containers."""
        ...
