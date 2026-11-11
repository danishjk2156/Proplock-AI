"""Feature 4 Phase 0 spike: prove the Invoicing v2 shapes against the real PayPal sandbox.

Through the same adapter the app uses:
  1. create a draft invoice (deposit + brokerage, the visit token as an invoice-level discount)
  2. create it again with the same PayPal-Request-Id: must return the same invoice
  3. read it back: the discount and the total must be what we sent
  4. send it (the sandbox recipient gets an email; nothing is charged)
  5. generate its QR code
  6. cancel it

Run from backend/:  .venv/Scripts/python -m scripts.spike_invoicing
Exit code 0 = all passed, 1 = a check failed, 2 = no sandbox keys.
"""

import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.paypal import PayPalError
from app.adapters.paypal_invoicing import InvoiceLine, InvoiceRequest, PayPalInvoicing
from app.config import get_settings


def main() -> int:
    settings = get_settings()
    if not (settings.paypal_client_id and settings.paypal_client_secret):
        print("No PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in backend/.env.")
        return 2
    if "sandbox" not in settings.paypal_api_base:
        print(f"Refusing to run against {settings.paypal_api_base}: this spike is for the sandbox only.")
        return 1

    api = PayPalInvoicing(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_api_base)
    tag = uuid.uuid4().hex[:10].upper()
    req = InvoiceRequest(
        number=f"PL-SPIKE-{tag}",
        currency="USD",
        lines=(
            InvoiceLine("Security deposit", "Signed PropLock audit SPIKE, deposit", Decimal("700.00")),
            InvoiceLine("Brokerage fee", "Signed PropLock audit SPIKE, brokerage", Decimal("350.00")),
        ),
        discount=Decimal("15.00"),
        recipient_name="Spike Tenant",
        recipient_email="spike-tenant@example.com",
        due_date=(datetime.now(UTC) + timedelta(days=7)).date(),
        note="Visit token already paid (PayPal capture SPIKE-CAPTURE) is deducted.",
        reference=f"spike-{tag}",
    )
    ok = True
    try:
        print(f"1) Create draft {req.number} ...")
        first = api.create_draft(req, request_id=f"spike-create-{tag}")
        print(f"   PASS: {first.paypal_invoice_id} status={first.status}")

        print("2) Same request id again ...")
        again = api.create_draft(req, request_id=f"spike-create-{tag}")
        same = again.paypal_invoice_id == first.paypal_invoice_id
        ok &= same
        print(f"   {'PASS' if same else 'FAIL'}: got {again.paypal_invoice_id}")

        print("3) Read back ...")
        state = api.get(first.paypal_invoice_id)
        amount = state.raw.get("amount", {})
        total = amount.get("value")
        discount = amount.get("breakdown", {}).get("discount", {})
        print("   amount =", json.dumps(amount))
        good_total = total == "1035.00"
        ok &= good_total
        print(f"   {'PASS' if good_total else 'FAIL'}: total {total} (expected 1035.00), discount {json.dumps(discount)}")

        print("4) Send ...")
        link = api.send(first.paypal_invoice_id, request_id=f"spike-send-{tag}")
        state = api.get(first.paypal_invoice_id)
        print(f"   {'PASS' if state.status in ('SENT', 'UNPAID') else 'FAIL'}: status={state.status} link={link}")
        print(f"   recipient_view_url={state.payer_view_url}")
        ok &= state.status in ("SENT", "UNPAID")

        print("5) QR code ...")
        qr = api.qr_code(first.paypal_invoice_id)
        print(f"   {'PASS' if len(qr) > 100 else 'FAIL'}: {len(qr)} chars, starts {qr[:24]!r}")
        ok &= len(qr) > 100

        print("6) Cancel ...")
        api.cancel(first.paypal_invoice_id, request_id=f"spike-cancel-{tag}", note="Spike invoice, never meant to be paid.")
        state = api.get(first.paypal_invoice_id)
        print(f"   {'PASS' if state.status == 'CANCELLED' else 'FAIL'}: status={state.status}")
        ok &= state.status == "CANCELLED"
    except PayPalError as e:
        print(f"   FAIL: {e}")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
