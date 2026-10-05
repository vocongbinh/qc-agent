"""Authentication module for qc-agent."""

from auth.antigravity import (
    get_valid_antigravity_credentials,
    run_antigravity_login,
)

__all__ = [
    "get_valid_antigravity_credentials",
    "run_antigravity_login",
]
