"""PayPalOrders (Orders v2, intent=AUTHORIZE) against a mock HTTP server, the fake twin, and the choice."""

import json
from decimal import Decimal

import httpx
import pytest

from app.adapters import paypal_orders as module
from app.adapters.paypal import PayPalError
from app.adapters.paypal_orders import DemoOrders, FakeOrders, OrderRequest, PayPalOrders, UnconfiguredOrders
from app.config import Settings

BASE = "https://api-m.sandbox.paypal.com"
TOKEN = httpx.Response(200, json={"access_token": "tok-123", "expires_in": 32400})
REQ = OrderRequest("visit-1", Decimal("15"), "USD", "PropLock visit hold")
AUTH = {"id": "AUTH-1", "status": "CREATED", "amount": {"currency_code": "USD", "value": "15.00"},
        "expiration_time": "2026-12-11T10:00:00Z"}
ORDER_WITH_AUTH = {"id": "ORDER-1", "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [AUTH]}}]}


def _api(routes):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = routes[(request.method, request.url.path)]
        return reply(request) if callable(reply) else reply

    return PayPalOrders("id", "secret", BASE, client=httpx.Client(transport=httpx.MockTransport(handler))), requests


def test_create_is_an_authorize_order_with_our_request_id():
    api, requests = _api({("POST", "/v1/oauth2/token"): TOKEN,
                          ("POST", "/v2/checkout/orders"): httpx.Response(201, json={"id": "ORDER-1", "status": "CREATED"})})
    assert api.create_authorize_order(REQ, request_id="visit:visit-1:create") == "ORDER-1"
    sent = requests[-1]
    body = json.loads(sent.content)
    assert body["intent"] == "AUTHORIZE"
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "15.00"}
    assert body["purchase_units"][0]["custom_id"] == "visit-1"
    assert sent.headers["PayPal-Request-Id"] == "visit:visit-1:create"


def test_authorize_returns_the_authorization_and_its_expiry():
    api, requests = _api({("POST", "/v1/oauth2/token"): TOKEN,
                          ("POST", "/v2/checkout/orders/ORDER-1/authorize"): httpx.Response(201, json=ORDER_WITH_AUTH)})
    auth = api.authorize("ORDER-1", request_id="r1")
    assert (auth.id, auth.status, auth.amount) == ("AUTH-1", "CREATED", Decimal("15.00"))
    assert auth.expires_at.year == 2026 and requests[-1].headers["PayPal-Request-Id"] == "r1"


def test_an_already_authorized_order_is_read_back_not_failed():
    api, _ = _api({("POST", "/v1/oauth2/token"): TOKEN,
                   ("POST", "/v2/checkout/orders/ORDER-1/authorize"): httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"}),
                   ("GET", "/v2/checkout/orders/ORDER-1"): httpx.Response(200, json=ORDER_WITH_AUTH)})
    assert api.authorize("ORDER-1", request_id="r1").id == "AUTH-1"


def test_capture_is_final_and_an_already_captured_one_is_found_on_the_order():
    cap = {"id": "CAP-1", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "15.00"}}
    api, requests = _api({("POST", "/v1/oauth2/token"): TOKEN,
                          ("POST", "/v2/payments/authorizations/AUTH-1/capture"): httpx.Response(201, json=cap)})
    assert api.capture("AUTH-1", "ORDER-1", request_id="visit:v:capture").id == "CAP-1"
    assert json.loads(requests[-1].content) == {"final_capture": True}

    order = {"purchase_units": [{"payments": {"captures": [cap]}}]}
    api, _ = _api({("POST", "/v1/oauth2/token"): TOKEN,
                   ("POST", "/v2/payments/authorizations/AUTH-1/capture"): httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"}),
                   ("GET", "/v2/checkout/orders/ORDER-1"): httpx.Response(200, json=order)})
    assert api.capture("AUTH-1", "ORDER-1", request_id="x").id == "CAP-1"


def test_void_is_done_when_paypal_says_already_voided():
    api, _ = _api({("POST", "/v1/oauth2/token"): TOKEN,
                   ("POST", "/v2/payments/authorizations/AUTH-1/void"): httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"}),
                   ("GET", "/v2/payments/authorizations/AUTH-1"): httpx.Response(200, json={**AUTH, "status": "VOIDED"})})
    api.void("AUTH-1", request_id="v")  # no error


def test_real_failures_raise_with_the_debug_id():
    api, _ = _api({("POST", "/v1/oauth2/token"): TOKEN,
                   ("POST", "/v2/payments/authorizations/AUTH-1/void"): httpx.Response(
                       500, json={"name": "INTERNAL_SERVER_ERROR"}, headers={"PayPal-Debug-Id": "dbg-9"})})
    with pytest.raises(PayPalError) as e:
        api.void("AUTH-1", request_id="v")
    assert e.value.status == 500 and e.value.debug_id == "dbg-9"


def test_fake_is_idempotent_needs_approval_and_settles_once():
    fake = FakeOrders(needs_approval=True)
    order = fake.create_authorize_order(REQ, "c")
    assert fake.create_authorize_order(REQ, "c") == order
    with pytest.raises(PayPalError, match="ORDER_NOT_APPROVED"):
        fake.authorize(order, "a")
    fake.approve(order)
    auth = fake.authorize(order, "a")
    assert fake.authorize(order, "a") == auth
    cap = fake.capture(auth.id, order, "cap")
    assert fake.capture(auth.id, order, "cap") == cap and len(fake.captures) == 1
    with pytest.raises(PayPalError, match="ALREADY_CAPTURED"):
        fake.void(auth.id, "void")


@pytest.mark.parametrize("settings, expected", [
    ({"paypal_client_id": "a", "paypal_client_secret": "b"}, PayPalOrders),
    ({"demo_mode": True}, DemoOrders),
    ({}, UnconfiguredOrders),
])
def test_adapter_choice(monkeypatch, settings, expected):
    monkeypatch.setattr(module, "get_settings", lambda: Settings(_env_file=None, **settings))
    module.get_orders.cache_clear()
    try:
        assert type(module.get_orders()) is expected
    finally:
        module.get_orders.cache_clear()
    assert DemoOrders().create_authorize_order(REQ, "x").startswith("FAKE-ORDER-")
