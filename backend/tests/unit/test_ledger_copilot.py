"""Feature 4 ledger copilot: questions become checked, read-only queries; write requests are refused."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agents.ledger_copilot import CopilotReply, CopilotUnavailable, Filter, LedgerQuery, WRITE_INTENT
from app.db.models import Invoice
from tests.f4_helpers import closed, demo_mode, keys  # noqa: F401  (fixtures)

UNPAID_3_DAYS = CopilotReply(query=LedgerQuery(target="closings", filters=[
    Filter(field="invoice_status", op="in", value=["SENT", "DRAFT", "PENDING"]),
    Filter(field="days_since_sent", op="gt", value=3)]))


def _ask(client, keys, q, who="broker_rajesh"):
    return client.post("/ledger/copilot", json={"question": q}, headers=keys[who]).json()


def test_unpaid_after_three_days_returns_the_right_rows(client, keys, demo_mode, fake_query_llm, db_session):
    old = closed(client, keys)
    invoice = db_session.scalar(select(Invoice).where(Invoice.closing_id == old["id"]))
    invoice.sent_at = datetime.now(UTC) - timedelta(days=5)
    db_session.commit()
    fake_query_llm.reply = UNPAID_3_DAYS

    out = _ask(client, keys, "Which deposits are unpaid after 3 days?")

    assert out["refused"] is False and out["target"] == "closings"
    assert [r["id"] for r in out["rows"]] == [old["id"]]
    assert out["answer"].startswith("1 closing match.")
    assert "Asha Rao" in out["answer"]  # the answer quotes the rows it used

    invoice.sent_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    assert _ask(client, keys, "Which deposits are unpaid after 3 days?")["rows"] == []


@pytest.mark.parametrize("q", ["cancel all invoices", "Please refund Asha", "can you send a reminder to everyone",
                               "Mark invoice PL-1 as paid", "delete the ledger"])
def test_write_requests_are_refused_before_the_model_is_asked(client, keys, fake_query_llm, q):
    out = _ask(client, keys, q)
    assert out["refused"] is True and out["rows"] == []
    assert fake_query_llm.calls == []


@pytest.mark.parametrize("q", ["which tenants still have to pay?", "show cancelled invoices", "what was sent this week"])
def test_read_questions_are_not_mistaken_for_writes(q):
    assert WRITE_INTENT.search(q) is None


def test_the_models_own_refusal_is_passed_on(client, keys, fake_query_llm):
    fake_query_llm.reply = CopilotReply(refuse="that would change an invoice")
    out = _ask(client, keys, "zero out every balance")
    assert out["refused"] is True and "change an invoice" in out["answer"]


def test_no_model_means_no_guess(client, keys, fake_query_llm):
    fake_query_llm.error = CopilotUnavailable("GROQ_API_KEY is not set")
    out = _ask(client, keys, "what is unpaid?")
    assert out["refused"] is True and "couldn't turn that into a ledger search" in out["answer"]


def test_brokers_only_query_their_own_rows(client, keys, demo_mode, fake_query_llm):
    closed(client, keys)
    fake_query_llm.reply = CopilotReply(query=LedgerQuery(target="entries"))
    assert _ask(client, keys, "show all entries", who="broker_quickcash")["rows"] == []
    assert len(_ask(client, keys, "show all entries", who="manager")["rows"]) >= 4


def test_queries_are_validated():
    with pytest.raises(ValidationError, match="unknown field"):
        LedgerQuery(target="closings", filters=[Filter(field="password", op="eq", value="x")])
    with pytest.raises(ValidationError):
        LedgerQuery(target="entries", limit=10_000)
    with pytest.raises(ValidationError, match="exactly one"):
        CopilotReply()
