"""Event facts remain inputs for bounded semantic risk judgment."""

from ...facts.risk import build_event_facts
from ...facts.models import EventFacts
from ...models.evidence import EventRiskAssessment


def assess_event_risk(
    state: str = "NORMAL",
    *,
    reasons=(),
    evidence_ids=(),
    unresolved: bool = False,
) -> EventRiskAssessment:
    """Validate a bounded event-risk judgment without scoring arithmetic."""
    return EventRiskAssessment(
        state=state,
        reasons=tuple(reasons),
        evidence_ids=tuple(evidence_ids),
        unresolved=unresolved,
    )


__all__ = [
    "EventFacts",
    "EventRiskAssessment",
    "assess_event_risk",
    "build_event_facts",
]
