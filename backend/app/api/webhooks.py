"""POST /webhooks/paypal: PayPal calls this; nothing is applied unless PayPal verifies the event."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.adapters.paypal_invoicing import WebhookVerifier, get_webhook_verifier
from app.db.base import get_session
from app.services import webhook_service
from app.services.webhook_service import WebhookRejected

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/paypal")
async def paypal_webhook(
    request: Request,
    session: Session = Depends(get_session),
    verifier: WebhookVerifier = Depends(get_webhook_verifier),
) -> dict[str, str]:
    raw = await request.body()  # the exact bytes: re-serialising would break signature checks
    try:
        outcome, message = webhook_service.receive(session, raw, dict(request.headers), verifier)
    except WebhookRejected as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"outcome": outcome, "message": message}
