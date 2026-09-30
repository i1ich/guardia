import sys
from pathlib import Path

import pytest

from guardia_agent.classify import classify_by_rules, make_classify_node, make_llm_classifier, severity_for
from guardia_agent.graph import build_triage_graph
from guardia_agent.plan import PLANNERS, plan_node_name
from guardia_agent.state import INCIDENT_CLASSES, UNDETERMINED
from tools.read import (
    DependencyProbeArgs,
    GetMetricsArgs,
    ParamMetadataArgs,
    QueryLogsArgs,
    RecentDeploymentsArgs,
    SearchRunbookArgs,
    StackResourcesArgs,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infrastructure" / "lambda" / "t5-intake"))
import intake  # noqa: E402

ARGS_MODELS = {
    "query_logs": QueryLogsArgs,
    "get_metrics": GetMetricsArgs,
    "recent_deployments": RecentDeploymentsArgs,
    "stack_resources": StackResourcesArgs,
    "param_metadata": ParamMetadataArgs,
    "dependency_probe": DependencyProbeArgs,
    "search_runbook": SearchRunbookArgs,
}
OPENED = "2026-09-29T15:41:00Z"
NOW = 1_790_696_460.0  # 2026-09-29T15:41:00Z + 0 minutes


def alarm(function="photolist-analyze-photo", metric="Errors", reason="Threshold Crossed"):
    message = {
        "AlarmName": f"{function}-{metric.lower()}",
        "NewStateValue": "ALARM",
        "NewStateReason": reason,
        "StateChangeTime": "2026-09-29T15:41:00.000+0000",
        "Trigger": {
            "MetricName": metric,
            "Namespace": "AWS/Lambda",
            "Statistic": "SUM",
            "Dimensions": [{"name": "FunctionName", "value": function}],
            "Period": 300,
            "EvaluationPeriods": 1,
            "Threshold": 1.0,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
    }
    return intake.normalize(message)


def run(envelope, llm=None):
    graph = build_triage_graph(llm_classify=llm)
    return graph.invoke({"envelope": envelope, "now_epoch": NOW})


# ---- classify -------------------------------------------------------------


def test_metric_rules():
    assert classify_by_rules(alarm(metric="Throttles")) == "cost-throttling-anomaly"
    assert classify_by_rules(alarm(metric="Duration")) == "lambda-timeout-cold-start"
    assert classify_by_rules(alarm(metric="Errors")) is None


def test_severity_marks_the_analyze_path_high():
    assert severity_for(alarm("photolist-analyze-photo")) == "high"
    assert severity_for(alarm("photolist-generate-upload-url")) == "medium"


def test_errors_alarm_without_llm_is_undetermined_not_guessed():
    result = run(alarm(metric="Errors"))
    assert result["incident_class"] == UNDETERMINED
    assert result["classification_source"] == "fallback:no-llm"


def test_llm_resolves_an_errors_alarm():
    result = run(alarm(metric="Errors"), llm=lambda envelope: "ml-token-expiry")
    assert result["incident_class"] == "ml-token-expiry"
    assert result["classification_source"] == "llm"


def test_llm_never_sees_secrets_in_the_alarm_reason():
    seen = {}

    def spy(envelope):
        seen.update(envelope)
        return "deploy-regression"

    reason = "auth failed for ops@example.com with APP_USR-123456789-093015-abcdef0123456789abcdef0123456789-42"
    run(alarm(metric="Errors", reason=reason), llm=spy)
    assert "ops@example.com" not in seen["reason"]
    assert "APP_USR-" not in seen["reason"]
    assert "[REDACTED-EMAIL-1]" in seen["reason"]


def test_llm_error_degrades_to_the_broad_plan():
    def boom(envelope):
        raise RuntimeError("model unavailable")

    result = run(alarm(metric="Errors"), llm=boom)
    assert result["incident_class"] == UNDETERMINED
    assert result["classification_source"] == "fallback:llm-error"
    assert result["evidence_plan"]  # still plans


def test_llm_answer_outside_the_allowed_classes_is_rejected():
    result = run(alarm(metric="Errors"), llm=lambda envelope: "cost-throttling-anomaly")
    assert result["incident_class"] == UNDETERMINED
    assert result["classification_source"] == "fallback:llm-invalid-class"


def test_rules_win_and_the_llm_is_not_called_for_throttles():
    def must_not_run(envelope):
        raise AssertionError("LLM called for a rules-decidable alarm")

    result = run(alarm(metric="Throttles"), llm=must_not_run)
    assert result["incident_class"] == "cost-throttling-anomaly"
    assert result["classification_source"] == "rules"


def test_make_llm_classifier_uses_structured_output():
    class FakeStructured:
        def invoke(self, prompt):
            assert "Envelope:" in prompt
            return type("R", (), {"incident_class": "ml-api-403-search"})()

    class FakeChat:
        def with_structured_output(self, schema):
            assert schema.__name__ == "ClassificationResult"
            return FakeStructured()

    assert make_llm_classifier(FakeChat())({"alarm_name": "x"}) == "ml-api-403-search"


# ---- plan + routing -------------------------------------------------------


@pytest.mark.parametrize("incident_class", [*INCIDENT_CLASSES, UNDETERMINED])
@pytest.mark.parametrize("function,metric", [("photolist-analyze-photo", "Errors"), ("leaselens-analyze-worker", "Duration")])
def test_every_planned_step_is_a_valid_call_to_a_real_tool(incident_class, function, metric):
    from guardia_agent.plan import _Ctx

    plan = PLANNERS[incident_class](_Ctx({"envelope": alarm(function, metric), "now_epoch": NOW}))
    assert plan
    for step in plan:
        assert set(step) == {"tool", "args", "purpose"}
        ARGS_MODELS[step["tool"]](**step["args"])  # raises on any schema violation


def test_graph_routes_each_class_to_its_own_plan_node():
    graph = build_triage_graph().get_graph()
    targets = {e.target for e in graph.edges if e.source == "classify"}
    assert targets == {plan_node_name(c) for c in [*INCIDENT_CLASSES, UNDETERMINED]}


def test_run_visits_only_the_plan_node_for_its_class():
    graph = build_triage_graph()
    visited = [
        name for chunk in graph.stream({"envelope": alarm(metric="Duration"), "now_epoch": NOW}) for name in chunk
    ]
    assert visited == ["classify", "plan_lambda_timeout_cold_start"]


def test_cold_start_plan_is_strictly_smaller_than_the_dependency_class():
    cold = run(alarm(metric="Duration"))["evidence_plan"]
    dependency = run(alarm(metric="Errors"), llm=lambda e: "ml-api-403-search")["evidence_plan"]
    broad = run(alarm(metric="Errors"))["evidence_plan"]
    assert len(cold) < len(dependency)
    assert len(cold) < len(broad)


def test_photolist_only_steps_are_not_planned_for_leaselens():
    plan = run(alarm("leaselens-analyze-worker", "Errors"))["evidence_plan"]
    tools = [(s["tool"], s["args"].get("name")) for s in plan]
    assert ("dependency_probe", "mercadolibre-api") not in tools
    assert not any(t == "param_metadata" for t, _ in tools)
    assert next(s for s in plan if s["tool"] == "recent_deployments")["args"]["stack"] == "LeaseLensApiStack"


def test_windows_reach_back_past_the_incident_start():
    late = {"envelope": alarm(metric="Errors"), "now_epoch": NOW + 3 * 3600}
    plan = build_triage_graph().invoke(late)["evidence_plan"]
    logs = next(s for s in plan if s["tool"] == "query_logs")
    assert logs["args"]["window_minutes"] >= 180 + 30


def test_unroutable_class_fails_loudly():
    from guardia_agent.classify import route_by_class

    with pytest.raises(ValueError):
        route_by_class({"incident_class": "made-up"})
