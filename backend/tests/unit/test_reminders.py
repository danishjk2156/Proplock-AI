"""Feature 4 reminders: due-1, due+1, due+3 on a fake clock; at most three; none after payment."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Invoice, JobStatus, LedgerEntry, OutboxJob
from app.services import outbox
from app.services.closing_service import REMIND_INVOICE
from app.services.invoice_jobs import reminder_times
from tests.f4_helpers import closed, demo_mode, keys  # noqa: F401  (fixtures)


def _invoice(db_session) -> Invoice:
    invoice = db_session.scalar(select(Invoice))
    db_session.refresh(invoice)
    return invoice


def test_reminder_days_come_from_settings_and_skip_the_past():
    due = datetime(2026, 11, 18, tzinfo=UTC)
    assert reminder_times(due, now=datetime(2026, 11, 11, tzinfo=UTC)) == [
        due - timedelta(days=1), due + timedelta(days=1), due + timedelta(days=3)]
    assert reminder_times(due, now=due) == [due + timedelta(days=1), due + timedelta(days=3)]


def test_three_reminders_on_schedule_then_nothing(client, keys, demo_mode, fake_invoicing, db_session):
    closed(client, keys)
    invoice = _invoice(db_session)
    jobs = db_session.scalars(select(OutboxJob).where(OutboxJob.kind == REMIND_INVOICE)).all()
    assert len(jobs) == 3

    def run(at):
        outbox.run_due(db_session, None, now=at, invoicing=fake_invoicing)
        return [c for c in fake_invoicing.calls if c[0] == "remind"]

    assert run(invoice.due_date - timedelta(days=2)) == []  # nothing is due yet
    assert len(run(invoice.due_date - timedelta(days=1))) == 1
    assert len(run(invoice.due_date + timedelta(days=1))) == 2
    assert len(run(invoice.due_date + timedelta(days=30))) == 3
    assert len(run(invoice.due_date + timedelta(days=60))) == 3  # never a fourth

    assert _invoice(db_session).reminders_sent == 3
    reminded = db_session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "INVOICE_REMINDED")).all()
    assert [e.direction.value for e in reminded] == ["info"] * 3


def test_no_reminder_after_the_invoice_is_paid(client, keys, demo_mode, fake_invoicing, db_session):
    body = closed(client, keys)
    fake_invoicing.mark_paid(body["invoice"]["paypal_invoice_id"], body["invoice"]["total"])
    client.post(f"/closings/{body['id']}/refresh", headers=keys["broker_rajesh"])
    invoice = _invoice(db_session)

    outbox.run_due(db_session, None, now=invoice.due_date + timedelta(days=30), invoicing=fake_invoicing)

    assert [c for c in fake_invoicing.calls if c[0] == "remind"] == []
    jobs = db_session.scalars(select(OutboxJob).where(OutboxJob.kind == REMIND_INVOICE)).all()
    assert {j.status for j in jobs} == {JobStatus.DONE}  # each one ended without calling PayPal
