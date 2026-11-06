"""PayPalCatalog against a mock HTTP server, and how an adapter is chosen."""

import json

import httpx
import pytest

from app.adapters import paypal as paypal_module
from app.adapters.paypal import (
    DemoPayPal,
    PayPalCatalog,
    PayPalError,
    Product,
    UnconfiguredPayPal,
    get_paypal,
)
from app.config import Settings

BASE = "https://api-m.sandbox.paypal.com"
PRODUCT = Product(id="PL-v1", name="PropLock verified: 2BHK", description="PropLock verified rental listing.")


def _catalog(routes):
    """routes: {(method, path): response or callable(request)}; every request is recorded."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = routes[(request.method, request.url.path)]
        return reply(request) if callable(reply) else reply

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PayPalCatalog("client-id", "client-secret", BASE, client=client), requests


TOKEN = httpx.Response(200, json={"access_token": "tok-123", "expires_in": 32400})


def test_creates_the_product_with_our_id_as_the_request_id():
    created = httpx.Response(201, json={"id": "PL-v1", "name": PRODUCT.name})
    paypal, requests = _catalog({("POST", "/v1/oauth2/token"): TOKEN, ("POST", "/v1/catalogs/products"): created})

    assert paypal.create_product(PRODUCT) == "PL-v1"

    login, create = requests
    assert login.headers["Authorization"].startswith("Basic ")  # client id + secret
    assert create.headers["Authorization"] == "Bearer tok-123"
    assert create.headers["PayPal-Request-Id"] == "PL-v1"
    assert json.loads(create.content) == {
        "id": "PL-v1", "name": PRODUCT.name, "description": PRODUCT.description, "type": "SERVICE"
    }


def test_access_token_is_reused_until_it_expires():
    created = httpx.Response(201, json={"id": "PL-v1"})
    paypal, requests = _catalog({("POST", "/v1/oauth2/token"): TOKEN, ("POST", "/v1/catalogs/products"): created})

    paypal.create_product(PRODUCT)
    paypal.create_product(PRODUCT)

    assert [r.url.path for r in requests].count("/v1/oauth2/token") == 1


def test_existing_product_with_our_id_counts_as_created():
    paypal, _ = _catalog({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v1/catalogs/products"): httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"}),
        ("GET", "/v1/catalogs/products/PL-v1"): httpx.Response(200, json={"id": "PL-v1"}),
    })

    assert paypal.create_product(PRODUCT) == "PL-v1"


def test_rejection_raises_with_paypals_debug_id():
    paypal, _ = _catalog({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v1/catalogs/products"): httpx.Response(
            400, json={"name": "INVALID_REQUEST"}, headers={"PayPal-Debug-Id": "abc123"}
        ),
        ("GET", "/v1/catalogs/products/PL-v1"): httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"}),
    })

    with pytest.raises(PayPalError) as error:
        paypal.create_product(PRODUCT)
    assert (error.value.status, error.value.debug_id) == (400, "abc123")
    assert "INVALID_REQUEST" in str(error.value)


def test_server_error_raises_without_looking_for_an_existing_product():
    paypal, requests = _catalog({
        ("POST", "/v1/oauth2/token"): TOKEN,
        ("POST", "/v1/catalogs/products"): httpx.Response(503, json={"name": "SERVICE_UNAVAILABLE"}),
    })

    with pytest.raises(PayPalError, match="HTTP 503"):
        paypal.create_product(PRODUCT)
    assert all(r.method == "POST" for r in requests)


def test_bad_credentials_raise():
    paypal, _ = _catalog({("POST", "/v1/oauth2/token"): httpx.Response(401, json={"error": "invalid_client"})})

    with pytest.raises(PayPalError, match="PayPal login failed"):
        paypal.create_product(PRODUCT)


def test_network_failure_raises():
    def down(request):
        raise httpx.ConnectError("no network", request=request)

    paypal, _ = _catalog({("POST", "/v1/oauth2/token"): down})

    with pytest.raises(PayPalError, match="PayPal unreachable: ConnectError"):
        paypal.create_product(PRODUCT)


def test_product_respects_paypal_length_limits():
    with pytest.raises(ValueError):
        Product(id="PL-v1", name="x" * 128, description="ok")
    with pytest.raises(ValueError):
        Product(id="PL-v1", name="ok", description="x" * 257)


def test_demo_ids_say_they_are_fake():
    assert DemoPayPal().create_product(PRODUCT) == "FAKE-PL-v1"


def test_unconfigured_never_returns_an_id():
    with pytest.raises(PayPalError, match="not set"):
        UnconfiguredPayPal("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set").create_product(PRODUCT)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"paypal_client_id": "id", "paypal_client_secret": "s", "demo_mode": True}, PayPalCatalog),
        ({"paypal_client_id": None, "paypal_client_secret": None, "demo_mode": True}, DemoPayPal),
        ({"paypal_client_id": None, "paypal_client_secret": None, "demo_mode": False}, UnconfiguredPayPal),
    ],
)
def test_get_paypal_picks_real_then_demo_then_none(monkeypatch, settings, expected):
    monkeypatch.setattr(paypal_module, "get_settings", lambda: Settings(_env_file=None, **settings))
    get_paypal.cache_clear()
    try:
        assert isinstance(get_paypal(), expected)
    finally:
        get_paypal.cache_clear()
