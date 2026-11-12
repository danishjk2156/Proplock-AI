"""PayPal Orders v2 with intent=AUTHORIZE: Feature 3's visit-fee hold.

create_authorize_order → (tenant approves in PayPal's popup) → authorize → later capture or void.
Every call that changes money carries a PayPal-Request-Id derived from our own visit id, so a
retry after a crash replays PayPal's first answer instead of acting twice. A 4xx on capture or
void is checked against PayPal's current state ("already voided" is success, not an error).

PayPalOrders is the real one (sandbox by default). DemoOrders (DEMO_MODE, no keys) and
FakeOrders (tests) keep state in memory; demo ids start with FAKE- so they can't pass as real.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

import httpx

from app.adapters.paypal import PayPalCatalog, PayPalError
from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderRequest:
    reference: str  # our visit id
    amount: Decimal
    currency: str
    description: str


@dataclass(frozen=True)
class Authorization:
    id: str
    status: str  # CREATED, CAPTURED, VOIDED, EXPIRED, PARTIALLY_CAPTURED, DENIED, PENDING
    amount: Decimal
    currency: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Capture:
    id: str
    status: str  # COMPLETED, PENDING, DECLINED
    amount: Decimal
    currency: str


class Orders(Protocol):
    name: str
    demo: bool  # True: no PayPal popup; the page shows a demo approve button instead

    def create_authorize_order(self, req: OrderRequest, request_id: str) -> str: ...
    def authorize(self, order_id: str, request_id: str) -> Authorization: ...
    def capture(self, authorization_id: str, order_id: str, request_id: str) -> Capture: ...
    def void(self, authorization_id: str, request_id: str) -> None: ...
    def get_authorization(self, authorization_id: str) -> Authorization: ...


def _money(currency: str, value: Decimal) -> dict:
    return {"currency_code": currency, "value": f"{Decimal(value):.2f}"}


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _authorization(body: dict) -> Authorization:
    amount = body.get("amount") or {}
    return Authorization(body["id"], body.get("status", ""), Decimal(amount.get("value", "0")),
                         amount.get("currency_code", ""), _when(body.get("expiration_time")))


class PayPalOrders(PayPalCatalog):
    name = "paypal-orders"
    demo = False

    def _fail(self, what: str, resp: httpx.Response) -> PayPalError:
        try:
            body = resp.json()
            issues = ", ".join(d.get("issue", "") for d in body.get("details", []) if d.get("issue"))
            detail = f"{body.get('name', '')} {issues}".strip()
        except ValueError:
            detail = ""
        debug_id = resp.headers.get("PayPal-Debug-Id")
        logger.error("PayPal %s failed: %s %s debug_id=%s", what, resp.status_code, detail, debug_id)
        return PayPalError(f"{what} failed {detail}".strip(), resp.status_code, debug_id)

    def create_authorize_order(self, req: OrderRequest, request_id: str) -> str:
        body = {
            "intent": "AUTHORIZE",
            "purchase_units": [{
                "reference_id": req.reference[:256],
                "custom_id": req.reference[:127],
                "description": req.description[:127],
                "amount": _money(req.currency, req.amount),
            }],
            "application_context": {"shipping_preference": "NO_SHIPPING", "user_action": "CONTINUE"},
        }
        resp = self._call("POST", "/v2/checkout/orders", json=body,
                          headers={"PayPal-Request-Id": request_id, "Prefer": "return=representation"})
        if resp.status_code not in (200, 201):
            raise self._fail("create order", resp)
        return resp.json()["id"]

    def authorize(self, order_id: str, request_id: str) -> Authorization:
        resp = self._call("POST", f"/v2/checkout/orders/{order_id}/authorize", json={},
                          headers={"PayPal-Request-Id": request_id, "Prefer": "return=representation"})
        if resp.status_code == 422:
            # ORDER_ALREADY_AUTHORIZED after a lost reply: read the authorization back.
            existing = self._order_authorization(order_id)
            if existing:
                return existing
        if resp.status_code not in (200, 201):
            raise self._fail("authorize order", resp)
        return _authorization(resp.json()["purchase_units"][0]["payments"]["authorizations"][0])

    def _order(self, order_id: str) -> dict:
        resp = self._call("GET", f"/v2/checkout/orders/{order_id}")
        if resp.status_code != 200:
            raise self._fail("get order", resp)
        return resp.json()

    def _order_authorization(self, order_id: str) -> Authorization | None:
        payments = (self._order(order_id).get("purchase_units") or [{}])[0].get("payments") or {}
        auths = payments.get("authorizations") or []
        return _authorization(auths[0]) if auths else None

    def capture(self, authorization_id: str, order_id: str, request_id: str) -> Capture:
        resp = self._call("POST", f"/v2/payments/authorizations/{authorization_id}/capture",
                          json={"final_capture": True},
                          headers={"PayPal-Request-Id": request_id, "Prefer": "return=representation"})
        if resp.status_code in (200, 201):
            body = resp.json()
            amount = body.get("amount") or {}
            return Capture(body["id"], body.get("status", ""), Decimal(amount.get("value", "0")), amount.get("currency_code", ""))
        if resp.status_code == 422:  # AUTHORIZATION_ALREADY_CAPTURED: find the capture on the order
            payments = (self._order(order_id).get("purchase_units") or [{}])[0].get("payments") or {}
            for c in payments.get("captures") or []:
                amount = c.get("amount") or {}
                return Capture(c["id"], c.get("status", ""), Decimal(amount.get("value", "0")), amount.get("currency_code", ""))
        raise self._fail("capture authorization", resp)

    def void(self, authorization_id: str, request_id: str) -> None:
        resp = self._call("POST", f"/v2/payments/authorizations/{authorization_id}/void",
                          headers={"PayPal-Request-Id": request_id})
        if resp.status_code in (200, 204):
            return
        if 400 <= resp.status_code < 500 and self.get_authorization(authorization_id).status in ("VOIDED", "EXPIRED"):
            return  # already released: the job is done
        raise self._fail("void authorization", resp)

    def get_authorization(self, authorization_id: str) -> Authorization:
        resp = self._call("GET", f"/v2/payments/authorizations/{authorization_id}")
        if resp.status_code != 200:
            raise self._fail("get authorization", resp)
        return _authorization(resp.json())


@dataclass
class _Order:
    req: OrderRequest
    approved: bool = False
    authorization: str | None = None


@dataclass
class FakeOrders:
    """In memory, idempotent by request id like PayPal. `fail_times` makes the next N calls
    fail like a 503. `needs_approval`: authorize fails until approve() (the buyer's popup)."""

    prefix: str = "FAKE-T-"
    needs_approval: bool = False
    fail_times: int = 0
    name: str = "fake-orders"
    demo: bool = True
    orders: dict[str, _Order] = field(default_factory=dict)
    authorizations: dict[str, Authorization] = field(default_factory=dict)
    captures: dict[str, Capture] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    _replies: dict[str, object] = field(default_factory=dict)

    def _maybe_fail(self, what: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise PayPalError(f"{what} failed SERVICE_UNAVAILABLE", 503, "fake-debug-id")

    def _once(self, what: str, request_id: str, make):
        self.calls.append((what, request_id))
        self._maybe_fail(what)
        if request_id not in self._replies:
            self._replies[request_id] = make()
        return self._replies[request_id]

    def create_authorize_order(self, req: OrderRequest, request_id: str) -> str:
        def make():
            order_id = f"{self.prefix}ORDER-{len(self.orders) + 1:04d}"
            self.orders[order_id] = _Order(req)
            return order_id
        return self._once("create", request_id, make)

    def approve(self, order_id: str) -> None:
        self.orders[order_id].approved = True

    def authorize(self, order_id: str, request_id: str) -> Authorization:
        def make():
            order = self.orders.get(order_id)
            if order is None:
                raise PayPalError("authorize order failed RESOURCE_NOT_FOUND", 404)
            if self.needs_approval and not order.approved:
                raise PayPalError("authorize order failed UNPROCESSABLE_ENTITY ORDER_NOT_APPROVED", 422)
            auth = Authorization(f"{self.prefix}AUTH-{len(self.authorizations) + 1:04d}", "CREATED",
                                 order.req.amount, order.req.currency, datetime.now(UTC))
            order.authorization = auth.id
            self.authorizations[auth.id] = auth
            return auth
        try:
            return self._once("authorize", request_id, make)
        except PayPalError:
            self._replies.pop(request_id, None)  # a refused call may be retried after approval
            raise

    def capture(self, authorization_id: str, order_id: str, request_id: str) -> Capture:
        def make():
            auth = self.authorizations[authorization_id]
            if auth.status == "VOIDED":
                raise PayPalError("capture authorization failed AUTHORIZATION_VOIDED", 422)
            cap = Capture(f"{self.prefix}CAP-{len(self.captures) + 1:04d}", "COMPLETED", auth.amount, auth.currency)
            self.captures[cap.id] = cap
            self.authorizations[authorization_id] = Authorization(auth.id, "CAPTURED", auth.amount, auth.currency)
            return cap
        return self._once("capture", request_id, make)

    def void(self, authorization_id: str, request_id: str) -> None:
        def make():
            auth = self.authorizations[authorization_id]
            if auth.status == "CAPTURED":
                raise PayPalError("void authorization failed AUTHORIZATION_ALREADY_CAPTURED", 422)
            self.authorizations[authorization_id] = Authorization(auth.id, "VOIDED", auth.amount, auth.currency)
            return None
        self._once("void", request_id, make)

    def get_authorization(self, authorization_id: str) -> Authorization:
        return self.authorizations[authorization_id]


class DemoOrders(FakeOrders):
    """DEMO_MODE only, no keys. Kept per process: a restart forgets demo holds (they were never real)."""

    def __init__(self):
        super().__init__(prefix="FAKE-", name="demo-orders")


class UnconfiguredOrders:
    name = "none"
    demo = False

    def __init__(self, reason: str):
        self._reason = reason

    def _no(self, *_args, **_kwargs):
        raise PayPalError(self._reason)

    create_authorize_order = authorize = capture = void = get_authorization = _no


@lru_cache
def get_orders() -> Orders:
    """FastAPI dependency. Real sandbox with keys; FAKE- ids only when DEMO_MODE is on."""
    s = get_settings()
    if s.paypal_client_id and s.paypal_client_secret:
        return PayPalOrders(s.paypal_client_id, s.paypal_client_secret, s.paypal_api_base)
    if s.demo_mode:
        return DemoOrders()
    return UnconfiguredOrders("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set and DEMO_MODE is off")
