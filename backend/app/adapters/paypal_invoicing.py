"""PayPal Invoicing v2, webhook verification and Customer Disputes v1 (Feature 4).

Same twin pattern as app/adapters/paypal.py:
- PayPalInvoicing: the real sandbox API (reuses PayPalCatalog's OAuth token cache and errors).
- DemoInvoicing: DEMO_MODE only, no keys; ids start with "FAKE-INV-" so they can't pass as real.
- FakeInvoicing: in-memory, for tests.
- UnconfiguredInvoicing: every call raises PayPalError, so a job waits instead of inventing an id.

Every state-changing call takes a request_id that the caller keeps fixed across retries
(PayPal-Request-Id), so a retried job replays PayPal's first answer instead of acting twice.
"""

import base64
import hashlib
import hmac
import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

import httpx

from app.adapters.paypal import PayPalCatalog, PayPalError
from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_ITEM_NAME = 200
MAX_NOTE = 4000


@dataclass(frozen=True)
class InvoiceLine:
    name: str
    description: str
    amount: Decimal


@dataclass(frozen=True)
class InvoiceRequest:
    """Everything PayPal needs to draft one invoice. Built from the signed Invoice row only."""

    number: str
    currency: str
    lines: tuple[InvoiceLine, ...]
    discount: Decimal  # the captured visit token, shown as an invoice-level discount
    recipient_name: str
    recipient_email: str
    due_date: date
    note: str
    reference: str  # our invoice id, so PayPal's copy points back at our record

    def __post_init__(self):
        if any(len(line.name) > MAX_ITEM_NAME for line in self.lines) or len(self.note) > MAX_NOTE:
            raise ValueError("PayPal limits: item name <= 200 chars, note <= 4000 chars")


@dataclass
class CreatedInvoice:
    paypal_invoice_id: str
    status: str


@dataclass
class InvoiceState:
    status: str  # PayPal's status: DRAFT, SENT, PAID, MARKED_AS_PAID, CANCELLED, REFUNDED, ...
    paid_amount: Decimal | None
    payer_view_url: str | None
    raw: dict = field(default_factory=dict)


def _money(currency: str, value: Decimal) -> dict:
    return {"currency_code": currency, "value": f"{value:.2f}"}


def invoice_body(req: InvoiceRequest) -> dict:
    """The Invoicing v2 create-draft body. Kept separate so the spike and tests check one shape."""
    given, _, surname = req.recipient_name.strip().partition(" ")
    name = {"given_name": given or req.recipient_name}
    if surname:
        name["surname"] = surname
    body = {
        "detail": {
            "invoice_number": req.number,
            "reference": req.reference,
            "currency_code": req.currency,
            "note": req.note,
            "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": req.due_date.isoformat()},
        },
        "invoicer": {"business_name": "PropLock verified rentals"},
        "primary_recipients": [{"billing_info": {"name": name, "email_address": req.recipient_email}}],
        "items": [
            {
                "name": line.name,
                "description": line.description,
                "quantity": "1",
                "unit_amount": _money(req.currency, line.amount),
                "unit_of_measure": "AMOUNT",
            }
            for line in req.lines
        ],
    }
    if req.discount > 0:
        body["amount"] = {"breakdown": {"discount": {"invoice_discount": {"amount": _money(req.currency, req.discount)}}}}
    return body


def _payer_link(body: dict) -> str | None:
    for link in body.get("links", []) or []:
        if link.get("rel") == "payer-view":
            return link.get("href")
    return None


def _state(body: dict) -> InvoiceState:
    paid = (body.get("payments") or {}).get("paid_amount")
    return InvoiceState(
        status=body.get("status", "UNKNOWN"),
        paid_amount=Decimal(paid["value"]) if paid else None,
        payer_view_url=(body.get("detail") or {}).get("metadata", {}).get("recipient_view_url") or _payer_link(body),
        raw=body,
    )


class Invoicing(Protocol):
    name: str

    def create_draft(self, req: InvoiceRequest, request_id: str) -> CreatedInvoice: ...
    def send(self, invoice_id: str, request_id: str) -> str | None: ...
    def remind(self, invoice_id: str, request_id: str, note: str) -> None: ...
    def cancel(self, invoice_id: str, request_id: str, note: str) -> None: ...
    def get(self, invoice_id: str) -> InvoiceState: ...
    def qr_code(self, invoice_id: str) -> str: ...
    def provide_dispute_evidence(self, dispute_id: str, notes: str, request_id: str) -> None: ...


class PayPalInvoicing(PayPalCatalog):
    name = "paypal-invoicing"

    def _fail(self, what: str, resp: httpx.Response) -> PayPalError:
        try:
            body = resp.json()
            detail = body.get("name", "") or body.get("error", "")
            issues = ", ".join(d.get("issue", "") for d in body.get("details", []) if d.get("issue"))
            detail = f"{detail} {issues}".strip()
        except ValueError:
            detail = ""
        debug_id = resp.headers.get("PayPal-Debug-Id")
        logger.error("PayPal %s failed: %s %s debug_id=%s", what, resp.status_code, detail, debug_id)
        return PayPalError(f"{what} failed {detail}".strip(), resp.status_code, debug_id)

    def create_draft(self, req: InvoiceRequest, request_id: str) -> CreatedInvoice:
        resp = self._call(
            "POST",
            "/v2/invoicing/invoices",
            json=invoice_body(req),
            headers={"PayPal-Request-Id": request_id, "Prefer": "return=representation"},
        )
        if resp.status_code == 422 and "DUPLICATE_INVOICE_NUMBER" in resp.text:
            # Seen in the sandbox: a retry with the same PayPal-Request-Id is NOT replayed; PayPal
            # refuses the number instead. Our number is fixed per invoice, so the invoice with that
            # number is the one an earlier attempt created: find it and treat that as success.
            existing = self.find_by_number(req.number)
            if existing:
                logger.info("PayPal invoice %s already existed as %s; treating as created", req.number, existing.paypal_invoice_id)
                return existing
        if resp.status_code not in (200, 201):
            raise self._fail("create_invoice", resp)
        body = resp.json()
        invoice_id = body.get("id") or str(body.get("href", "")).rstrip("/").rsplit("/", 1)[-1]
        if not invoice_id:
            raise PayPalError("create_invoice returned no invoice id", resp.status_code)
        return CreatedInvoice(paypal_invoice_id=invoice_id, status=body.get("status", "DRAFT"))

    def find_by_number(self, number: str) -> CreatedInvoice | None:
        resp = self._call("POST", "/v2/invoicing/search-invoices", json={"invoice_number": number})
        if resp.status_code != 200:
            raise self._fail("search_invoices", resp)
        for item in resp.json().get("items", []):
            if (item.get("detail") or {}).get("invoice_number") == number:
                return CreatedInvoice(paypal_invoice_id=item["id"], status=item.get("status", "DRAFT"))
        return None

    def send(self, invoice_id: str, request_id: str) -> str | None:
        resp = self._call(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/send",
            json={"send_to_invoicer": False, "send_to_recipient": True},
            headers={"PayPal-Request-Id": request_id},
        )
        if resp.status_code == 422 and "ALREADY_SENT" in resp.text.upper():
            return self.get(invoice_id).payer_view_url  # an earlier attempt sent it; that's success
        if resp.status_code not in (200, 202, 204):
            raise self._fail("send_invoice", resp)
        try:
            return resp.json().get("href")
        except ValueError:
            return None

    def remind(self, invoice_id: str, request_id: str, note: str) -> None:
        resp = self._call(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/remind",
            json={"subject": "Reminder: your PropLock move-in invoice", "note": note[:MAX_NOTE],
                  "send_to_invoicer": False, "send_to_recipient": True},
            headers={"PayPal-Request-Id": request_id},
        )
        if resp.status_code not in (200, 202, 204):
            raise self._fail("remind_invoice", resp)

    def cancel(self, invoice_id: str, request_id: str, note: str) -> None:
        resp = self._call(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/cancel",
            json={"subject": "PropLock invoice cancelled", "note": note[:MAX_NOTE],
                  "send_to_invoicer": False, "send_to_recipient": True},
            headers={"PayPal-Request-Id": request_id},
        )
        if resp.status_code == 422 and "CANCELLED" in resp.text.upper():
            return  # already cancelled by an earlier attempt
        if resp.status_code not in (200, 204):
            raise self._fail("cancel_invoice", resp)

    def get(self, invoice_id: str) -> InvoiceState:
        resp = self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")
        if resp.status_code != 200:
            raise self._fail("get_invoice", resp)
        return _state(resp.json())

    def qr_code(self, invoice_id: str) -> str:
        """A base64 PNG of the pay link. PayPal answers with the base64 text itself."""
        resp = self._call(
            "POST", f"/v2/invoicing/invoices/{invoice_id}/generate-qr-code", json={"width": 300, "height": 300}
        )
        if resp.status_code not in (200, 201):
            raise self._fail("generate_qr_code", resp)
        text = resp.text.strip()
        # Some responses arrive as multipart; the image is the last base64 run in the body.
        if "--" in text and "\n" in text:
            parts = [p.strip() for p in text.splitlines() if p.strip() and not p.startswith(("--", "Content-"))]
            text = parts[-1] if parts else text
        return text

    def provide_dispute_evidence(self, dispute_id: str, notes: str, request_id: str) -> None:
        payload = {"evidences": [{"evidence_type": "OTHER", "notes": notes[:2000]}]}
        resp = self._call(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            files={"input": (None, json.dumps(payload), "application/json")},
            headers={"PayPal-Request-Id": request_id},
        )
        if resp.status_code not in (200, 201, 202):
            raise self._fail("provide_dispute_evidence", resp)

    def verify_webhook(self, headers: dict[str, str], event: dict, webhook_id: str) -> bool:
        """Ask PayPal whether these headers really sign this event for our webhook."""
        h = {k.lower(): v for k, v in headers.items()}
        body = {
            "auth_algo": h.get("paypal-auth-algo"),
            "cert_url": h.get("paypal-cert-url"),
            "transmission_id": h.get("paypal-transmission-id"),
            "transmission_sig": h.get("paypal-transmission-sig"),
            "transmission_time": h.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        if not all(body.values()):
            return False
        resp = self._call("POST", "/v1/notifications/verify-webhook-signature", json=body)
        if resp.status_code != 200:
            raise self._fail("verify_webhook_signature", resp)
        return resp.json().get("verification_status") == "SUCCESS"


# A 1x1 transparent PNG: the demo "QR code", clearly not a real pay link.
_DEMO_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


class FakeInvoicing:
    """In-memory PayPal for tests. Idempotent per request id; `fail_times` fails the next N calls."""

    name = "fake-invoicing"

    def __init__(self, fail_times: int = 0, id_prefix: str = "INV2-FAKE-"):
        self.invoices: dict[str, dict] = {}
        self.by_request: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_times = fail_times
        self._ids = itertools.count(1)
        self._prefix = id_prefix

    def _maybe_fail(self, what: str):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise PayPalError(f"{what} failed SERVICE_UNAVAILABLE", 503, "fake-debug-id")

    def create_draft(self, req: InvoiceRequest, request_id: str) -> CreatedInvoice:
        self.calls.append(("create", request_id))
        self._maybe_fail("create_invoice")
        if request_id not in self.by_request:
            invoice_id = f"{self._prefix}{next(self._ids):04d}"
            self.by_request[request_id] = invoice_id
            self.invoices[invoice_id] = {"status": "DRAFT", "request": req, "body": invoice_body(req), "reminders": 0}
        return CreatedInvoice(self.by_request[request_id], "DRAFT")

    def _invoice(self, invoice_id: str) -> dict:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", 404, "fake-debug-id")
        return self.invoices[invoice_id]

    def send(self, invoice_id: str, request_id: str) -> str | None:
        self.calls.append(("send", request_id))
        self._maybe_fail("send_invoice")
        self._invoice(invoice_id)["status"] = "SENT"
        return f"https://www.sandbox.paypal.com/invoice/p/#{invoice_id}"

    def remind(self, invoice_id: str, request_id: str, note: str) -> None:
        self.calls.append(("remind", request_id))
        self._maybe_fail("remind_invoice")
        self._invoice(invoice_id)["reminders"] += 1

    def cancel(self, invoice_id: str, request_id: str, note: str) -> None:
        self.calls.append(("cancel", request_id))
        self._maybe_fail("cancel_invoice")
        self._invoice(invoice_id)["status"] = "CANCELLED"

    def get(self, invoice_id: str) -> InvoiceState:
        inv = self._invoice(invoice_id)
        paid = inv.get("paid")
        return InvoiceState(inv["status"], paid, f"https://www.sandbox.paypal.com/invoice/p/#{invoice_id}")

    def mark_paid(self, invoice_id: str, amount: Decimal) -> None:
        """Test helper: what the tenant paying on PayPal's page would do."""
        self._invoice(invoice_id).update(status="PAID", paid=amount)

    def qr_code(self, invoice_id: str) -> str:
        self._invoice(invoice_id)
        return _DEMO_PNG

    def provide_dispute_evidence(self, dispute_id: str, notes: str, request_id: str) -> None:
        self.calls.append(("evidence", request_id))
        self._maybe_fail("provide_dispute_evidence")


class DemoInvoicing(FakeInvoicing):
    """DEMO_MODE only, no keys: behaves like PayPal but every id says FAKE-INV-.
    Kept in memory per process, so a restart forgets demo invoices (they were never real)."""

    name = "demo-invoicing"

    def __init__(self):
        super().__init__(id_prefix="FAKE-INV-")


class UnconfiguredInvoicing:
    name = "none"

    def __init__(self, reason: str):
        self._reason = reason

    def _no(self, *_args, **_kwargs):
        raise PayPalError(self._reason)

    create_draft = send = remind = cancel = get = qr_code = provide_dispute_evidence = _no


@lru_cache
def get_invoicing() -> Invoicing:
    """FastAPI dependency. Real sandbox with keys; FAKE-INV- ids only when DEMO_MODE is on."""
    settings = get_settings()
    if settings.paypal_client_id and settings.paypal_client_secret:
        return PayPalInvoicing(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_api_base)
    if settings.demo_mode:
        return DemoInvoicing()
    return UnconfiguredInvoicing("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set and DEMO_MODE is off")


# --- Webhook verification ---


class WebhookVerifier(Protocol):
    name: str

    def verify(self, headers: dict[str, str], raw_body: bytes, event: dict) -> bool: ...


class PayPalWebhookVerifier:
    name = "paypal"

    def __init__(self, api: PayPalInvoicing, webhook_id: str):
        self._api = api
        self._webhook_id = webhook_id

    def verify(self, headers: dict[str, str], raw_body: bytes, event: dict) -> bool:
        return self._api.verify_webhook(headers, event, self._webhook_id)


DEMO_SIGNATURE_HEADER = "X-Demo-Signature"


def demo_signature(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


class DemoWebhookVerifier:
    """DEMO_MODE only: accepts events HMAC-signed with AUDIT_SECRET (the demo script signs them),
    so the demo can show "paid" without a public URL. Anything unsigned is still refused."""

    name = "demo"

    def __init__(self, secret: str):
        self._secret = secret

    def verify(self, headers: dict[str, str], raw_body: bytes, event: dict) -> bool:
        sent = {k.lower(): v for k, v in headers.items()}.get(DEMO_SIGNATURE_HEADER.lower(), "")
        return bool(sent) and hmac.compare_digest(demo_signature(raw_body, self._secret), sent)


class RejectAllVerifier:
    name = "none"

    def verify(self, headers: dict[str, str], raw_body: bytes, event: dict) -> bool:
        return False


@lru_cache
def get_webhook_verifier() -> WebhookVerifier:
    settings = get_settings()
    if settings.paypal_client_id and settings.paypal_client_secret and settings.paypal_webhook_id:
        api = PayPalInvoicing(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_api_base)
        return PayPalWebhookVerifier(api, settings.paypal_webhook_id)
    if settings.demo_mode:
        return DemoWebhookVerifier(settings.audit_secret)
    return RejectAllVerifier()


def png_data_url(b64: str) -> str:
    base64.b64decode(b64, validate=False)  # raises on garbage before it reaches a browser
    return f"data:image/png;base64,{b64}"
