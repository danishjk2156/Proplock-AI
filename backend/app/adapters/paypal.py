"""PayPal Catalog Products: publish a verified listing as a product (fixes M5).

PayPalCatalog is the real one (sandbox by default). DemoPayPal is used only with
DEMO_MODE=true and no keys; its ids start with "FAKE-" so they can never be mistaken for
real ones. FakePayPal is for tests. Every failure raises PayPalError: nothing ever turns a
failed call into a made-up product id.
"""

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_NAME = 127
MAX_DESCRIPTION = 256


@dataclass(frozen=True)
class Product:
    id: str  # ours, deterministic (PL-{version_id}), so a retry can never create a second product
    name: str
    description: str

    def __post_init__(self):
        if len(self.name) > MAX_NAME or len(self.description) > MAX_DESCRIPTION:
            raise ValueError("PayPal limits: name <= 127 chars, description <= 256 chars")


class PayPalError(Exception):
    def __init__(self, message: str, status: int | None = None, debug_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.debug_id = debug_id  # quote this to PayPal support

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.status:
            parts.append(f"HTTP {self.status}")
        if self.debug_id:
            parts.append(f"debug_id={self.debug_id}")
        return " | ".join(parts)


class PayPal(Protocol):
    name: str

    def create_product(self, product: Product) -> str:
        """Create the product (or confirm it already exists) and return PayPal's product id."""
        ...


class PayPalCatalog:
    name = "paypal"

    def __init__(self, client_id: str, client_secret: str, api_base: str, client: httpx.Client | None = None):
        self._auth = (client_id, client_secret)
        self._base = api_base.rstrip("/")
        self._client = client or httpx.Client(timeout=20)
        self._token: str | None = None
        self._token_expires = 0.0

    def _access_token(self) -> str:
        """OAuth2 client credentials, cached until a minute before it expires (not one login per call)."""
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        try:
            resp = self._client.post(
                f"{self._base}/v1/oauth2/token", auth=self._auth, data={"grant_type": "client_credentials"}
            )
        except httpx.HTTPError as e:
            raise PayPalError(f"PayPal unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise PayPalError("PayPal login failed", resp.status_code, resp.headers.get("PayPal-Debug-Id"))
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires = time.monotonic() + int(body.get("expires_in", 0)) - 60
        return self._token

    def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}", **kwargs.pop("headers", {})}
        try:
            return self._client.request(method, f"{self._base}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise PayPalError(f"PayPal unreachable: {type(e).__name__}") from e

    def create_product(self, product: Product) -> str:
        body = {"id": product.id, "name": product.name, "description": product.description, "type": "SERVICE"}
        resp = self._call(
            "POST",
            "/v1/catalogs/products",
            json=body,
            # Same request id on every retry: PayPal replays the first result instead of acting twice.
            headers={"PayPal-Request-Id": product.id, "Prefer": "return=representation"},
        )
        if resp.status_code in (200, 201):
            return resp.json()["id"]
        debug_id = resp.headers.get("PayPal-Debug-Id")
        if 400 <= resp.status_code < 500:
            # A product with our id may already exist (e.g. created by an earlier run that crashed
            # before saving). Our id is deterministic, so finding it means the job is done.
            existing = self._call("GET", f"/v1/catalogs/products/{product.id}")
            if existing.status_code == 200:
                logger.info("PayPal product %s already existed; treating as created", product.id)
                return existing.json()["id"]
        try:
            name = resp.json().get("name", "")
        except ValueError:
            name = ""
        logger.error("PayPal create_product failed: %s %s debug_id=%s", resp.status_code, name, debug_id)
        raise PayPalError(f"create_product failed {name}".strip(), resp.status_code, debug_id)


class DemoPayPal:
    """DEMO_MODE only: pretends to create the product, with an id that says it's fake."""

    name = "demo-paypal"

    def create_product(self, product: Product) -> str:
        return f"FAKE-{product.id}"


class UnconfiguredPayPal:
    name = "none"

    def __init__(self, reason: str):
        self._reason = reason

    def create_product(self, product: Product) -> str:
        raise PayPalError(self._reason)


class FakePayPal:
    """In-memory catalog for tests. `fail_times` makes the next N calls fail like a PayPal 503."""

    name = "fake-paypal"

    def __init__(self, fail_times: int = 0):
        self.products: dict[str, Product] = {}
        self.calls: list[Product] = []
        self.fail_times = fail_times

    def create_product(self, product: Product) -> str:
        self.calls.append(product)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise PayPalError("create_product failed SERVICE_UNAVAILABLE", 503, "fake-debug-id")
        self.products.setdefault(product.id, product)  # idempotent, like PayPal-Request-Id
        return product.id


@lru_cache
def get_paypal() -> PayPal:
    """FastAPI dependency. Real sandbox with keys; FAKE- ids only when DEMO_MODE is on."""
    settings = get_settings()
    if settings.paypal_client_id and settings.paypal_client_secret:
        return PayPalCatalog(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_api_base)
    if settings.demo_mode:
        return DemoPayPal()
    return UnconfiguredPayPal("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set and DEMO_MODE is off")
