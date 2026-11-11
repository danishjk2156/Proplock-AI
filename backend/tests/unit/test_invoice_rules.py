"""Feature 4 invoice rules: amounts come from the signed audit; the token is deducted; no negatives."""

from decimal import Decimal

import pytest

from app.domain.invoice_rules import InvoiceRefused, Override, build_invoice_draft

D = Decimal
BASE = dict(audit_id="A1", verified_deposit=D("70000"), verified_brokerage=D("35000"), token=D("15"), currency="USD")


def test_total_is_deposit_plus_brokerage_minus_token():
    draft = build_invoice_draft(**BASE)

    assert (draft.deposit, draft.brokerage, draft.token_deduction) == (D("70000.00"), D("35000.00"), D("15.00"))
    assert draft.total == D("104985.00")
    assert [l.name for l in draft.lines] == ["Security deposit", "Brokerage fee"]
    assert all("audit A1" in l.description for l in draft.lines)
    assert draft.overridden == ()


def test_same_amounts_written_differently_are_not_a_change():
    assert build_invoice_draft(**BASE, deposit=D("70000.00"), brokerage=D("35000")).overridden == ()


@pytest.mark.parametrize("change", [{"deposit": D("80000")}, {"brokerage": D("36000")}])
def test_an_amount_that_differs_from_the_audit_is_refused(change):
    with pytest.raises(InvoiceRefused, match="manager override"):
        build_invoice_draft(**BASE, **change)


def test_a_manager_override_with_a_note_is_allowed_and_flagged():
    draft = build_invoice_draft(**BASE, deposit=D("60000"), override=Override("M1", "Owner agreed a lower deposit by email"))

    assert draft.deposit == D("60000.00")
    assert draft.overridden == ("deposit",)
    assert "manager override" in draft.lines[0].description


def test_an_override_without_a_note_is_refused():
    with pytest.raises(InvoiceRefused, match="note"):
        build_invoice_draft(**BASE, deposit=D("60000"), override=Override("M1", "  "))


def test_a_token_larger_than_the_amount_due_is_refused_not_negative():
    with pytest.raises(InvoiceRefused, match="refund"):
        build_invoice_draft(**{**BASE, "verified_deposit": D("10"), "verified_brokerage": D("0")})


def test_zero_brokerage_leaves_one_line():
    draft = build_invoice_draft(**{**BASE, "verified_brokerage": D("0")})

    assert [l.name for l in draft.lines] == ["Security deposit"]
    assert draft.total == D("69985.00")


def test_negative_amounts_are_refused():
    with pytest.raises(InvoiceRefused, match="negative"):
        build_invoice_draft(**{**BASE, "token": D("-1")})
