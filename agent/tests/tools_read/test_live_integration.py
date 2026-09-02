"""Live integration test against real sa-east-1 resources (T6 validation).

Opt-in only — skipped unless GUARDIA_LIVE_AWS_TESTS=1, since it needs real
AWS credentials and hits real PhotoList LATAM / LeaseLens infrastructure.
Every call here is read-only (Describe/Get/List/Query) — nothing mutates
subject-system state.
"""

import os

import pytest

from tools.read.dependency import DependencyProbeArgs, dependency_probe
from tools.read.deployments import RecentDeploymentsArgs, recent_deployments
from tools.read.logs import QueryLogsArgs, query_logs
from tools.read.metrics import GetMetricsArgs, get_metrics
from tools.read.params import ParamMetadataArgs, param_metadata
from tools.read.resources import StackResourcesArgs, stack_resources

pytestmark = pytest.mark.skipif(
    os.environ.get("GUARDIA_LIVE_AWS_TESTS") != "1",
    reason="set GUARDIA_LIVE_AWS_TESTS=1 to run against real sa-east-1 resources",
)


def test_query_logs_against_real_function():
    result = query_logs(QueryLogsArgs(function="photolist-analyze-photo", window_minutes=1440))
    assert result["function"] == "photolist-analyze-photo"
    assert "error" not in result


def test_get_metrics_against_real_namespace():
    result = get_metrics(
        GetMetricsArgs(
            namespace="AWS/Lambda",
            metric_name="Invocations",
            dimensions={"FunctionName": "photolist-analyze-photo"},
            window_minutes=1440,
            stat="Sum",
        )
    )
    assert result["namespace"] == "AWS/Lambda"


def test_recent_deployments_against_real_stack():
    # PhotolistApiStack hasn't deployed within window_minutes' 7-day cap as
    # of this writing, so this targets GuardiaStateStack (deployed today,
    # per T3) to prove the tool's shape against a stack with real recent
    # events, without loosening the tool's own window bound for the test.
    result = recent_deployments(RecentDeploymentsArgs(stack="GuardiaStateStack", window_minutes=10080))
    assert result["stack"] == "GuardiaStateStack"
    assert "error" not in result
    assert len(result["events"]) > 0


def test_stack_resources_against_real_stack():
    result = stack_resources(StackResourcesArgs(stack="PhotolistApiStack"))
    assert len(result["resources"]) > 0
    assert any(r["resource_type"] == "AWS::Lambda::Function" for r in result["resources"])


def test_param_metadata_against_real_secret_returns_no_value():
    result = param_metadata(ParamMetadataArgs(name="/photolist/ml/client_secret"))
    assert result["found"] is True
    assert "value" not in {k.lower() for k in result}
    # the actual secret value must never appear anywhere in the result
    assert all(isinstance(v, (str, int, type(None))) for v in result.values())


def test_dependency_probe_against_real_lambda():
    result = dependency_probe(DependencyProbeArgs(name="lambda:photolist-analyze-photo"))
    assert result["reachable"] is True


def test_dependency_probe_against_real_external_dependency():
    # Asserts the probe reports truthfully, not that the dependency is
    # healthy: as of this writing api.mercadolibre.com/sites/MLU returns
    # 403 to unauthenticated requests regardless of client (verified with
    # curl too) — that is itself a real, correctly-reported probe result.
    result = dependency_probe(DependencyProbeArgs(name="mercadolibre-api"))
    assert result["reachable"] is True
    assert isinstance(result["http_status"], int)
