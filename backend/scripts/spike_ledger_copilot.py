"""Feature 4 Phase 6 spike: ten real questions through Groq -> LedgerQuery.

Checks that the real text model returns a valid query (or a refusal) for each question, and
that write requests are refused. No database is touched.

Run from backend/:  .venv/Scripts/python -m scripts.spike_ledger_copilot
Exit code 0 = all as expected, 1 = some failed, 2 = no Groq key/model.
"""

import sys
from datetime import UTC, datetime

from app.agents.ledger_copilot import WRITE_INTENT, CopilotUnavailable, GroqQueryLLM
from app.config import get_settings

QUESTIONS = [
    ("Which deposits are unpaid after 3 days?", "query"),
    ("Show every invoice that has been paid", "query"),
    ("How much is still outstanding for Asha Rao?", "query"),
    ("List the ledger entries from webhooks", "query"),
    ("Which closings are disputed?", "query"),
    ("Invoices with more than one reminder sent", "query"),
    ("Biggest outstanding balances first", "query"),
    ("Which visit holds were voided?", "query"),
    ("What's the weather in Bengaluru?", "refuse"),
    ("Zero out every unpaid balance for me", "refuse"),
]


def main() -> int:
    s = get_settings()
    if not (s.groq_api_key and s.groq_text_model):
        print("No GROQ_API_KEY / GROQ_TEXT_MODEL in backend/.env.")
        return 2
    llm = GroqQueryLLM(s.groq_api_key, s.groq_text_model)
    ok = True
    for question, expected in QUESTIONS:
        if WRITE_INTENT.search(question):
            got, detail = "refuse", "refused by rule before the model"
        else:
            try:
                reply = llm.to_query(question, datetime.now(UTC).date())
                got = "refuse" if reply.refuse else "query"
                detail = reply.refuse or reply.query.model_dump_json(exclude_defaults=True)
            except CopilotUnavailable as e:
                got, detail = "error", str(e)
        passed = got == expected
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {question}\n      -> {got}: {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
