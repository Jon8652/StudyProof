"""Validate the live coach's confidence assessment."""
from app.constants import CONFIDENCE_LEVELS
from app.core.agent.provider_base import ProviderError


def resolve_feedback_confidence(question, reply):
    supplied = (reply.meta or {}).get('confidence') if reply is not None else None
    if isinstance(supplied, str) and supplied in CONFIDENCE_LEVELS:
        return supplied
    raise ProviderError('The AI reply was missing a valid confidence rating. Please try again.')
