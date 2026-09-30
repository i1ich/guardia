"""Shared graph state for the triage graph (T9 onward)."""

from __future__ import annotations

from typing import Any, TypedDict

INCIDENT_CLASSES = (
    "ml-api-403-search",
    "ml-token-expiry",
    "deploy-regression",
    "lambda-timeout-cold-start",
    "cost-throttling-anomaly",
)
# Routing value for "the envelope alone cannot tell": gets the broad plan.
UNDETERMINED = "undetermined"


class IncidentState(TypedDict, total=False):
    envelope: dict[str, Any]  # from T5 intake
    incident_class: str  # one of INCIDENT_CLASSES or UNDETERMINED
    severity: str  # low | medium | high | critical
    classification_source: str  # rules | llm | fallback:<reason>
    evidence_plan: list[dict[str, Any]]  # [{"tool", "args", "purpose"}]
    evidence: list[dict[str, Any]]
    now_epoch: float  # test seam; defaults to time.time()
    _redactor: Any
