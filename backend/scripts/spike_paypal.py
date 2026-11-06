"""Phase 7 spike: prove the PayPal sandbox keys work with the real Catalog Products API.

Makes real sandbox calls through the same adapter the app uses:
  1. OAuth2 client-credentials login
  2. create one product with a deterministic id and a <=256-char description
  3. create it again with the same id: must return the same product (idempotent), not a second one

Run from backend/:  .venv/Scripts/python -m scripts.spike_paypal
Exit code 0 = all passed, 1 = a check failed, 2 = no sandbox keys.
"""

import sys
import uuid

from app.adapters.paypal import PayPalCatalog, PayPalError, Product
from app.config import get_settings


def main() -> int:
    settings = get_settings()
    if not (settings.paypal_client_id and settings.paypal_client_secret):
        print("No PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET in backend/.env.")
        print("Create a sandbox app at https://developer.paypal.com/dashboard/applications/sandbox and add both.")
        return 2
    if "sandbox" not in settings.paypal_api_base:
        print(f"Refusing to run against {settings.paypal_api_base}: this spike is for the sandbox only.")
        return 1

    paypal = PayPalCatalog(settings.paypal_client_id, settings.paypal_client_secret, settings.paypal_api_base)
    product = Product(
        id=f"PL-SPIKE-{uuid.uuid4().hex[:12]}",
        name="PropLock spike product",
        description="PropLock Phase 7 spike: one sandbox product to prove the Catalog Products API works.",
    )
    try:
        print(f"1+2) Login and create {product.id} ...")
        first = paypal.create_product(product)
        print(f"   PASS: created {first}")
        print("3) Create the same id again ...")
        second = paypal.create_product(product)
        print(f"   {'PASS' if second == first else 'FAIL'}: got {second}")
        return 0 if second == first else 1
    except PayPalError as e:
        print(f"   FAIL: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
