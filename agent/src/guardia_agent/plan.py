"""The plan nodes (T9): which evidence is worth collecting for this class.

One node per class, reached through a conditional edge from `classify`
(see graph.py) — the routing is a real graph edge, not an `if` inside one
node. Each node emits a structured evidence plan: an ordered list of
read-only tool calls (`tool` + `args` matching the T6 tool schemas) with
the reason each is worth its tokens. `gather` (T10) executes it.

Plans are deliberately different sizes: a cold-start incident needs three
looks, an incident that could be a third-party dependency needs five, and
an undetermined one gets the broad set.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Callable

from guardia_agent.state import INCIDENT_CLASSES, UNDETERMINED, IncidentState

STACK_FOR_SYSTEM = {"photolist-latam": "PhotolistApiStack", "lease-lens": "LeaseLensApiStack"}
ML_PARAMS = ("/photolist/ml/refresh_token", "/photolist/ml/client_id")

_LOG_CAP_MINUTES = 1440
_METRIC_CAP_MINUTES = 720
_DEPLOY_CAP_MINUTES = 10080


def _step(tool: str, purpose: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "purpose": purpose}


def _logs_filter(pattern: str) -> str:
    return f"@message like /(?i){pattern}/"


class _Ctx:
    """Envelope facts the planners need, plus the tool-call step builders."""

    def __init__(self, state: IncidentState):
        envelope = state["envelope"]
        metric = envelope.get("metric") or {}
        self.function: str | None = (metric.get("dimensions") or {}).get("FunctionName")
        self.metric_name: str = metric.get("name") or "Errors"
        self.system: str = envelope["source_system"]
        self.stack: str | None = STACK_FOR_SYSTEM.get(self.system)
        self.is_photolist = self.system == "photolist-latam"

        now = state.get("now_epoch") or time.time()
        opened = datetime.fromisoformat(envelope["timestamp"].replace("Z", "+00:00")).timestamp()
        age_minutes = max(0, math.ceil((now - opened) / 60))
        # The tools look back from *now*, so reach past the incident start with margin.
        self.log_window = min(max(age_minutes + 30, 15), _LOG_CAP_MINUTES)
        self.metric_window = min(max(age_minutes + 60, 60), _METRIC_CAP_MINUTES)
        self.deploy_window = min(self.metric_window * 3, _DEPLOY_CAP_MINUTES)

    def logs(self, pattern: str, purpose: str) -> list[dict]:
        if not self.function:
            return []
        return [
            _step(
                "query_logs", purpose, function=self.function,
                window_minutes=self.log_window, filter=_logs_filter(pattern),
            )
        ]

    def metric(self, name: str, stat: str, purpose: str) -> list[dict]:
        if not self.function:
            return []
        return [
            _step(
                "get_metrics", purpose, namespace="AWS/Lambda", metric_name=name,
                dimensions={"FunctionName": self.function},
                window_minutes=self.metric_window, period_seconds=300, stat=stat,
            )
        ]

    def deployments(self, purpose: str) -> list[dict]:
        if not self.stack:
            return []
        return [_step("recent_deployments", purpose, stack=self.stack, window_minutes=self.deploy_window)]

    def resources(self, purpose: str) -> list[dict]:
        return [_step("stack_resources", purpose, stack=self.stack)] if self.stack else []

    def ml_probe(self) -> list[dict]:
        if not self.is_photolist:
            return []
        return [_step("dependency_probe", "Is the MercadoLibre API reachable and answering?", name="mercadolibre-api")]

    def ml_params(self) -> list[dict]:
        if not self.is_photolist:
            return []
        return [
            _step("param_metadata", "Token parameter age/version (metadata only, never the value).", name=name)
            for name in ML_PARAMS
        ]

    def runbook(self, query: str) -> list[dict]:
        return [_step("search_runbook", "Retrieve the operator's own procedure for this class.", query=query)]


def _cold_start(c: _Ctx) -> list[dict]:
    return (
        c.metric("Duration", "Maximum", "Did duration approach the timeout, and when did it start?")
        + c.logs("Init Duration|Task timed out", "Cold-start init cost or timeout lines.")
        + c.runbook("Lambda timeout or cold start latency")
    )


def _ml_403(c: _Ctx) -> list[dict]:
    return (
        c.logs("403|forbidden|mercadolibre", "Where and how often the 403s appear.")
        + c.ml_probe()
        + c.metric("Errors", "Sum", "Error volume over time.")
        + c.deployments("Did a deploy precede the 403s?")
        + c.runbook("MercadoLibre API returns 403 on search")
    )


def _ml_token(c: _Ctx) -> list[dict]:
    return (
        c.logs("401|expired|invalid_token|unauthorized", "Auth failures pointing at the token.")
        + c.ml_params()
        + c.ml_probe()
        + c.runbook("MercadoLibre access or refresh token expired")
    )


def _deploy(c: _Ctx) -> list[dict]:
    return (
        c.deployments("What changed recently, and when.")
        + c.logs("exception|error", "First errors after the change.")
        + c.metric("Errors", "Sum", "Did errors begin at a deploy boundary?")
        + c.resources("Current resources and versions of the stack.")
        + c.runbook("Deploy regression rollback or redeploy")
    )


def _throttling(c: _Ctx) -> list[dict]:
    return (
        c.metric("Throttles", "Sum", "Throttle volume over time.")
        + c.metric("Invocations", "Sum", "Was traffic unusually high?")
        + c.metric("ConcurrentExecutions", "Maximum", "Concurrency against the limit.")
        + c.resources("Stack resources (reserved concurrency, event sources).")
        + c.runbook("Lambda throttling or cost anomaly")
    )


def _undetermined(c: _Ctx) -> list[dict]:
    stat = "Maximum" if c.metric_name == "Duration" else "Sum"
    return (
        c.deployments("What changed recently, and when.")
        + c.logs("exception|error|timed out|403|401", "Broad sweep of failure signatures.")
        + c.metric(c.metric_name, stat, "The alarming metric over time.")
        + c.ml_probe()
        + c.ml_params()
        + c.runbook("Lambda errors after an alarm, cause unknown")
    )


PLANNERS: dict[str, Callable[[_Ctx], list[dict]]] = {
    "ml-api-403-search": _ml_403,
    "ml-token-expiry": _ml_token,
    "deploy-regression": _deploy,
    "lambda-timeout-cold-start": _cold_start,
    "cost-throttling-anomaly": _throttling,
    UNDETERMINED: _undetermined,
}
assert set(PLANNERS) == set(INCIDENT_CLASSES) | {UNDETERMINED}


def plan_node_name(incident_class: str) -> str:
    return "plan_" + incident_class.replace("-", "_")


def make_plan_node(incident_class: str):
    planner = PLANNERS[incident_class]

    def plan_node(state: IncidentState) -> dict[str, Any]:
        return {"evidence_plan": planner(_Ctx(state))}

    return plan_node
