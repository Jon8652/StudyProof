"""Deterministic guards for the coach's background confidence assessment."""
import re
from app.constants import CONFIDENCE_LEVELS
from app.core.agent.provider_base import ProviderError

# Learner messages that refuse or express uncertainty are always attempts to
# answer: they must reach the scorer (which rates them 0-39) instead of being
# silently dropped as "not an answer". This keeps weak-topic flagging reliable
# even when the model misclassifies the message.
UNCERTAIN_ANSWER_RE = re.compile(
    r"^\s*(?:i\s*(?:'?m)?\s*(?:do(?:n'?t| not)?\s+know|donno)"
    r"|no\s+i\s+don'?t\s+know"
    r"|(?:i\s*)?'?m?\s*not\s+sure"
    r"|i\s+am\s+not\s+sure"
    r"|i\s+have\s+no\s+idea"
    r"|no\s+idea"
    r"|i\s+cannot\s+answer|i\s+can'?t\s+answer"
    r"|dunno"
    r"|beats\s+me)\s*[.!?]*\s*$",
    re.IGNORECASE)


def is_uncertain_answer(text):
    """True for bare refusals/uncertainty such as "I'm not sure"."""
    return bool(text and UNCERTAIN_ANSWER_RE.match(text))


def resolve_feedback_confidence(question, reply):
    supplied = (reply.meta or {}).get('confidence') if reply is not None else None
    if isinstance(supplied, str) and supplied in CONFIDENCE_LEVELS:
        return supplied
    raise ProviderError('The AI reply was missing a valid confidence rating. Please try again.')
