"""Feature 3 Phase 0 spike: prove the Orders v2 AUTHORIZE shapes against the real PayPal sandbox.

Through the same adapter the app uses:
  1. create two $15.00 AUTHORIZE orders; creating one again with the same PayPal-Request-Id
     must return the same order id
  2. you approve both in a browser with a SANDBOX buyer account (the script prints the links
     and waits; PropLock never sees the buyer's login)
  3. authorize both; the second authorize call replays the first
  4. capture order 1 (twice: one capture), void order 2 (twice: still voided)
  5. read both authorizations back: CAPTURED and VOIDED, with the honor-period expiry printed

Run from backend/:  .venv/Scripts/python -m scripts.spike_orders [--wait-minutes 10] [--create-only]
  --create-only   stop after step 1 (checks keys and the request shape; needs no buyer)
Exit code 0 = all passed, 1 = a check failed, 2 = no sandbox keys, 3 = not approved in time.
"""

import argparse
import sys
import time
import uuid
from decimal import Decimal

from app.adapters.paypal import PayPalError
from app.adapters.paypal_orders import OrderRequest, PayPalOrders
from app.config import get_settings


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-minutes", type=float, default=10)
    parser.add_argument("--create-only", action="store_true")
    args = parser.parse_args()
    s = get_settings()
    if not (s.paypal_client_id and s.paypal_client_secret):
        print("No PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in backend/.env.")
        return 2
    if "sandbox" not in s.paypal_api_base:
        print(f"Refusing to run against {s.paypal_api_base}: this spike is for the sandbox only.")
        return 1
    api = PayPalOrders(s.paypal_client_id, s.paypal_client_secret, s.paypal_api_base)
    tag = uuid.uuid4().hex[:8].upper()
    ok = True

    try:
        print("1. Create two AUTHORIZE orders")
        orders = []
        for n in (1, 2):
            req = OrderRequest(f"SPIKE-{tag}-{n}", Decimal("15.00"), "USD", f"PropLock spike visit hold {n}")
            first = api.create_authorize_order(req, request_id=f"spike-{tag}-{n}-create")
            again = api.create_authorize_order(req, request_id=f"spike-{tag}-{n}-create")
            ok &= check(f"order {n} created once", first == again, first)
            orders.append(first)
        if args.create_only:
            print("\nStopped after step 1 (--create-only).")
            return 0 if ok else 1

        print("\n2. Approve both orders with a SANDBOX buyer, in your browser:")
        for order_id in orders:
            print(f"   https://www.sandbox.paypal.com/checkoutnow?token={order_id}")
        deadline = time.monotonic() + args.wait_minutes * 60
        pending = set(orders)
        while pending and time.monotonic() < deadline:
            for order_id in list(pending):
                if api._order(order_id).get("status") == "APPROVED":
                    print(f"   approved: {order_id}")
                    pending.discard(order_id)
            time.sleep(5)
        if pending:
            print(f"Not approved within {args.wait_minutes} minutes: {', '.join(pending)}")
            return 3

        print("\n3. Authorize")
        auths = []
        for n, order_id in enumerate(orders, 1):
            a = api.authorize(order_id, request_id=f"spike-{tag}-{n}-authorize")
            b = api.authorize(order_id, request_id=f"spike-{tag}-{n}-authorize")
            ok &= check(f"order {n} authorized once", a.id == b.id and a.status == "CREATED", f"{a.id} expires {a.expires_at}")
            auths.append(a)

        print("\n4. Capture order 1, void order 2")
        c1 = api.capture(auths[0].id, orders[0], request_id=f"spike-{tag}-capture")
        c2 = api.capture(auths[0].id, orders[0], request_id=f"spike-{tag}-capture")
        ok &= check("captured once", c1.id == c2.id and c1.amount == Decimal("15.00"), f"{c1.id} {c1.status}")
        api.void(auths[1].id, request_id=f"spike-{tag}-void")
        api.void(auths[1].id, request_id=f"spike-{tag}-void")
        ok &= check("voided (twice is fine)", True)

        print("\n5. Read back")
        ok &= check("order 1 authorization", api.get_authorization(auths[0].id).status == "CAPTURED")
        ok &= check("order 2 authorization", api.get_authorization(auths[1].id).status == "VOIDED")
    except PayPalError as e:
        print(f"PayPal error: {e}")
        return 1
    print(f"\n{'All checks passed.' if ok else 'Some checks FAILED.'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
