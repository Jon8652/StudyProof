"""Injectable provider contract."""
from typing import Protocol
from app.core.agent.schema import CoachReply


class ProviderError(Exception):
    """Only friendly, secret-free messages cross the provider boundary."""


class CoachProvider(Protocol):
    def generate(self, context: dict) -> CoachReply: ...

