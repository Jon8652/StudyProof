"""AI-assigned confidence (replaces the manual Guessed/Unsure/Confident buttons).

The learner no longer self-reports confidence.  Instead, every feedback turn
carries a coach-assessed ``confidence`` value:

* live mode: the coach model rates how well the question was answered and
  returns ``meta.confidence`` ('low' | 'medium' | 'high');
* mock/offline mode (and live replies missing or carrying an invalid rating):
  a deterministic extractive fallback grades the answer against the source
  keywords and the cloze answer key, using the first unaided answer before
  any revision is applied.

Only 'low', 'medium' and 'high' (CONFIDENCE_LEVELS) are ever stored, so all
downstream statistics keep working unchanged.
"""
from app.constants import CONFIDENCE_LEVELS
from app.core.agent.mock_engine import keywords


def _ratio(answer, target):
    """Fraction of distinct long words from *target* present in *answer*."""
    target_words = keywords(target)
    if not target_words:
        return None
    return len(target_words & keywords(answer)) / len(target_words)


def assess_confidence(question):
    """Deterministically rate how well *question* was answered (mock path).

    Returns 'low', 'medium' or 'high'.  The first unaided answer is graded
    against the cloze answer key when one exists, otherwise against the
    question's source spans; hints seen count against the learner.
    """
    answer = question.first_answer or ''
    if not answer.strip():
        return 'low'
    target = question.answer_key.get('sentence') or ' '.join(
        span.get('text', '') for span in question.spans)
    ratio = _ratio(answer, target)
    if ratio is None:
        return 'medium'
    if question.hints_seen >= 2:
        ratio -= 0.15          # needed a lot of nudging toward the idea
    if ratio >= 0.6:
        return 'high'
    if ratio >= 0.3:
        return 'medium'
    return 'low'


def resolve_feedback_confidence(question, reply):
    """Pick the confidence level to store for a completed feedback turn.

    Prefers the model-supplied ``reply.meta['confidence']`` when it is one of
    the three valid levels; otherwise falls back to the deterministic
    assessment above.  Always returns a valid level once an answer exists.
    """
    supplied = (reply.meta or {}).get('confidence') if reply is not None else None
    if isinstance(supplied, str) and supplied in CONFIDENCE_LEVELS:
        return supplied
    return assess_confidence(question)

