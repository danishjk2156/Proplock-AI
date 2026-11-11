"""Build the move-in invoice from the signed audit. Pure: no database, no network.

The rule that stops hidden charges at signing: the deposit and brokerage on the invoice must
be exactly what the signed Feature 1 audit verified for that listing version. Anything else
needs a manager's override with a written reason, which the ledger records.
"""

from dataclasses import dataclass
from decimal import Decimal

CENT = Decimal("0.01")


class InvoiceRefused(Exception):
    """The invoice would break a money rule. The message says which one."""


@dataclass(frozen=True)
class Override:
    manager_id: str
    note: str


@dataclass(frozen=True)
class DraftLine:
    name: str
    description: str
    amount: Decimal


@dataclass(frozen=True)
class InvoiceDraft:
    deposit: Decimal
    brokerage: Decimal
    token_deduction: Decimal
    total: Decimal
    currency: str
    lines: tuple[DraftLine, ...]
    overridden: tuple[str, ...]  # which amounts differ from the audit (empty without an override)

    @property
    def gross(self) -> Decimal:
        return self.deposit + self.brokerage


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT)


def build_invoice_draft(
    *,
    audit_id: str,
    verified_deposit: Decimal,
    verified_brokerage: Decimal,
    token: Decimal,
    currency: str,
    deposit: Decimal | None = None,
    brokerage: Decimal | None = None,
    override: Override | None = None,
) -> InvoiceDraft:
    """deposit / brokerage default to the verified amounts. Different ones need `override`."""
    verified = {"deposit": _q(verified_deposit), "brokerage": _q(verified_brokerage)}
    asked = {
        "deposit": _q(deposit) if deposit is not None else verified["deposit"],
        "brokerage": _q(brokerage) if brokerage is not None else verified["brokerage"],
    }
    token = _q(token)
    for name, value in (*asked.items(), ("token", token)):
        if value < 0:
            raise InvoiceRefused(f"{name} cannot be negative")

    differs = tuple(k for k in asked if asked[k] != verified[k])
    if differs and override is None:
        rows = ", ".join(f"{k} {asked[k]} vs verified {verified[k]}" for k in differs)
        raise InvoiceRefused(f"Amounts differ from signed audit {audit_id} ({rows}); a manager override with a note is required")
    if override is not None and not override.note.strip():
        raise InvoiceRefused("A manager override needs a note saying why")

    gross = asked["deposit"] + asked["brokerage"]
    if gross <= 0:
        raise InvoiceRefused("Nothing to invoice: deposit and brokerage are both zero")
    total = gross - token
    if total < 0:
        # Refunding the excess token is a separate flow (not built); never send a negative invoice.
        raise InvoiceRefused(f"The visit token ({token}) is more than the amount due ({gross}); refund it instead")

    lines = tuple(
        DraftLine(label, f"Verified in PropLock audit {audit_id}" + (" (manager override)" if key in differs else ""), asked[key])
        for key, label in (("deposit", "Security deposit"), ("brokerage", "Brokerage fee"))
        if asked[key] > 0
    )
    return InvoiceDraft(
        deposit=asked["deposit"],
        brokerage=asked["brokerage"],
        token_deduction=token,
        total=total,
        currency=currency,
        lines=lines,
        overridden=differs if override else (),
    )
