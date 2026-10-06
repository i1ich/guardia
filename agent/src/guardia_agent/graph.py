"""Triage graph: classify -> (conditional edge) -> plan_<class> -> gather
-> redact -> hypothesize -> (sufficiency edge) -> done | gather | handoff.

Without a `hypothesizer` the graph stops after the plan nodes (the T9 slice).
T11+ add the human gate after `finish`. No checkpointer is attached by
default; the caller compiles with the DynamoDB saver (see the T4 spike).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from guardia_agent.classify import LLMClassifier, make_classify_node, route_by_class
from guardia_agent.investigate import (
    DEFAULT_TOKEN_BUDGET,
    Hypothesizer,
    ToolRunner,
    default_tool_runner,
    finish_node,
    handoff_node,
    make_gather_node,
    make_hypothesize_node,
    redact_for_model,
    route_sufficiency,
)
from guardia_agent.plan import PLANNERS, make_plan_node, plan_node_name
from guardia_agent.state import IncidentState


def build_triage_graph(
    llm_classify: LLMClassifier | None = None,
    checkpointer=None,
    hypothesizer: Hypothesizer | None = None,
    tool_runner: ToolRunner = default_tool_runner,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
):
    graph = StateGraph(IncidentState)
    graph.add_node("classify", make_classify_node(llm_classify))
    for incident_class in PLANNERS:
        graph.add_node(plan_node_name(incident_class), make_plan_node(incident_class))
        graph.add_edge(plan_node_name(incident_class), "gather" if hypothesizer else END)
    if hypothesizer:
        graph.add_node("gather", make_gather_node(tool_runner))
        graph.add_node("redact", redact_for_model)
        graph.add_node("hypothesize", make_hypothesize_node(hypothesizer, token_budget))
        graph.add_node("finish", finish_node)
        graph.add_node("handoff", handoff_node)
        graph.add_edge("gather", "redact")
        graph.add_edge("redact", "hypothesize")
        graph.add_conditional_edges(
            "hypothesize", route_sufficiency, {"done": "finish", "gather": "gather", "handoff": "handoff"}
        )
        graph.add_edge("finish", END)
        graph.add_edge("handoff", END)
    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        route_by_class,
        {incident_class: plan_node_name(incident_class) for incident_class in PLANNERS},
    )
    return graph.compile(checkpointer=checkpointer)
