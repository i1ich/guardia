"""Triage graph, T9 slice: classify -> (conditional edge) -> plan_<class>.

T10 extends this with gather/hypothesize/sufficiency after the plan nodes;
T11+ add the human gate. No checkpointer is attached by default — the
caller compiles with the DynamoDB saver (see the T4 spike).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from guardia_agent.classify import LLMClassifier, make_classify_node, route_by_class
from guardia_agent.plan import PLANNERS, make_plan_node, plan_node_name
from guardia_agent.state import IncidentState


def build_triage_graph(llm_classify: LLMClassifier | None = None, checkpointer=None):
    graph = StateGraph(IncidentState)
    graph.add_node("classify", make_classify_node(llm_classify))
    for incident_class in PLANNERS:
        graph.add_node(plan_node_name(incident_class), make_plan_node(incident_class))
        graph.add_edge(plan_node_name(incident_class), END)
    graph.set_entry_point("classify")
    graph.add_conditional_edges(
        "classify",
        route_by_class,
        {incident_class: plan_node_name(incident_class) for incident_class in PLANNERS},
    )
    return graph.compile(checkpointer=checkpointer)
