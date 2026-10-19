"""Shared result type for application actions."""
from dataclasses import dataclass


@dataclass
class ActionResult:
    ok: bool
    message: str | None = None
    value: object | None = None
