"""Broker trust score: every change is a trust event, so the score can always be explained.

Points follow the PropLock design (Feature 1 review, section 11). Visit points (+1 visit
completed, -10 broker no-show) belong to Feature 3 and are not recorded here yet.
"""

from sqlalchemy.orm import Session

from app.db.models import TrustEvent, User

VERIFIED_FIRST_TRY = ("verified_first_try", 2)
NEEDS_FIX = ("needs_fix", -1)
PHOTO_REUSED = ("photo_reused_from_another_flat", -15)
EXPIRED_STALE = ("listing_expired_stale", -3)


def record(session: Session, broker_id: str, event: tuple[str, int], audit_id: str | None = None) -> TrustEvent:
    """Add (not commit) the event and apply it, inside the caller's transaction."""
    kind, points = event
    row = TrustEvent(broker_id=broker_id, kind=kind, points=points, audit_id=audit_id)
    session.add(row)
    broker = session.get(User, broker_id)
    broker.trust_score += points
    return row
