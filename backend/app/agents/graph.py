"""START -> extract -> fan-out to every check -> decide -> END.

Phase 2 wires in Check D only. Later phases add a node to CHECK_NODES and nothing else
changes: the fan-out, the fan-in and the list of required checks all follow from it.
"""

from collections.abc import Callable
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents import nodes
from app.agents.nodes import AuditState
from app.domain.check_result import Check, CheckResult
from app.domain.decision import Decision, decide
from app.domain.ground_truth import ListingTerms, OwnerTerms

CHECK_NODES: dict[Check, Callable[[AuditState], dict]] = {
    Check.GROUND_TRUTH: nodes.ground_truth,
}
REQUIRED_CHECKS = tuple(CHECK_NODES)


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


def run_checks(listing: ListingTerms, owner: OwnerTerms) -> tuple[list[CheckResult], Decision]:
    final = audit_graph().invoke({"listing": listing, "owner": owner, "results": []})
    return final["results"], final["decision"]
