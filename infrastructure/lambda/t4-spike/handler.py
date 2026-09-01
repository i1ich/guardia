"""T4 risk spike.

Proves the one architectural claim the whole project rests on: a LangGraph
graph can pause for a real gap between two separate Lambda invocations,
resuming from a DynamoDB checkpoint without re-running the node before the
interrupt, while billing only the seconds each invocation actually runs.

node_a / node_b are throwaway probes, not production agent code. They get
replaced once T9/T10 build the real classify -> plan -> gather graph.
"""

from __future__ import annotations

import os
import time
from typing import Any, TypedDict

import boto3
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from langgraph_checkpoint_aws import DynamoDBSaver

TABLE_NAME = os.environ["GUARDIA_CHECKPOINTS_TABLE"]
REGION = os.environ.get("AWS_REGION", "sa-east-1")

_dynamodb = boto3.client("dynamodb", region_name=REGION)


class SpikeState(TypedDict):
    counter: int
    resumed_with: Any


def _record_side_effect(thread_id: str) -> None:
    """The side effect that must fire exactly once across both invocations.

    Lives in the same table as the checkpoints (PK prefix keeps it from
    colliding with the checkpointer's own CHECKPOINT_/WRITES_/CHUNK_ items)
    so the whole spike needs no extra infrastructure.
    """
    _dynamodb.update_item(
        TableName=TABLE_NAME,
        Key={
            "PK": {"S": f"SPIKE_COUNTER_{thread_id}"},
            "SK": {"S": "side_effect"},
        },
        UpdateExpression="ADD side_effect_count :incr SET last_fired_at = :now",
        ExpressionAttributeValues={
            ":incr": {"N": "1"},
            ":now": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        },
    )


def node_a(state: SpikeState, config: RunnableConfig) -> dict:
    thread_id = config["configurable"]["thread_id"]
    _record_side_effect(thread_id)
    return {"counter": state["counter"] + 1}


def node_b(state: SpikeState) -> dict:
    approval = interrupt({"question": "approve resuming node_b?", "counter": state["counter"]})
    return {"resumed_with": approval}


def build_graph():
    checkpointer = DynamoDBSaver(table_name=TABLE_NAME, region_name=REGION)
    graph = StateGraph(SpikeState)
    graph.add_node("node_a", node_a)
    graph.add_node("node_b", node_b)
    graph.set_entry_point("node_a")
    graph.add_edge("node_a", "node_b")
    graph.add_edge("node_b", END)
    return graph.compile(checkpointer=checkpointer)


def _jsonable(result: dict) -> dict:
    result = dict(result)
    interrupts = result.get("__interrupt__")
    if interrupts:
        result["__interrupt__"] = [{"id": i.id, "value": i.value} for i in interrupts]
    return result


def handler(event: dict, context: Any) -> dict:
    mode = event["mode"]  # "start" | "resume"
    thread_id = event["thread_id"]
    config = {"configurable": {"thread_id": thread_id}}
    graph = build_graph()

    if mode == "start":
        result = graph.invoke({"counter": 0, "resumed_with": None}, config)
    elif mode == "resume":
        result = graph.invoke(Command(resume=event.get("resume_value", "approved")), config)
    else:
        raise ValueError(f"unknown mode {mode!r}, expected 'start' or 'resume'")

    return {
        "thread_id": thread_id,
        "mode": mode,
        "result": _jsonable(result),
        "remaining_time_ms": context.get_remaining_time_in_millis() if context else None,
    }
