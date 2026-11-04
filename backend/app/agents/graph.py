"""START -> extract -> fan-out to every check -> decide -> END.

Adding a check is one entry in CHECK_NODES: the fan-out, the fan-in and the list of
required checks all follow from it.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import NamedTuple

from langgraph.graph import END, START, StateGraph

from app.adapters.llm import LLM
from app.adapters.maps import Maps, Place
from app.agents import nodes
from app.agents.nodes import AuditState
from app.db.models import Furnishing
from app.domain.check_result import Check, CheckResult
from app.domain.claims import ClaimedCommute, CommuteClaimIn
from app.domain.decision import Decision, decide
from app.domain.ground_truth import ListingTerms, OwnerTerms

CHECK_NODES: dict[Check, Callable[[AuditState], dict]] = {
    Check.COMMUTE: nodes.commute,
    Check.COMPLIANCE: nodes.compliance,
    Check.GROUND_TRUTH: nodes.ground_truth,
}
# Reading the ad text is required too: if it failed, hidden claims could slip through.
REQUIRED_CHECKS = (Check.CLAIMS, *CHECK_NODES)


class AuditRun(NamedTuple):
    results: list[CheckResult]
    decision: Decision
    commute: list[ClaimedCommute]
    text_furnishing: Furnishing | None


def _decide(state: AuditState) -> dict:
    return {"decision": decide(state["results"], REQUIRED_CHECKS)}


@lru_cache
def audit_graph():
    graph = StateGraph(AuditState)
    graph.add_node("extract", nodes.extract_claims)
    graph.add_node("decide", _decide)
    graph.add_edge(START, "extract")
    for check, node in CHECK_NODES.items():
        graph.add_node(check.value, node)
        graph.add_edge("extract", check.value)  # fan-out: every check starts after extract
    # Fan-in: a list of sources means "decide" waits until all of them have finished.
    graph.add_edge([c.value for c in CHECK_NODES], "decide")
    graph.add_edge("decide", END)
    return graph.compile()


def run_checks(
    *,
    title: str,
    description: str,
    listing: ListingTerms,
    owner: OwnerTerms,
    place: Place,
    form_commute: list[CommuteClaimIn],
    llm: LLM,
    maps: Maps,
) -> AuditRun:
    final = audit_graph().invoke(
        {
            "title": title,
            "description": description,
            "listing": listing,
            "owner": owner,
            "place": place,
            "form_commute": form_commute,
            "results": [],
        },
        # Services go in the run config, not the state: the state is data, these are tools.
        config={"configurable": {"llm": llm, "maps": maps}},
    )
    return AuditRun(final["results"], final["decision"], final["commute"], final["text_furnishing"])
