from unittest.mock import patch

import boto3
from moto import mock_aws

from tools.read._common import REGION
from tools.read.dependency import DependencyProbeArgs, dependency_probe


@mock_aws
def test_dependency_probe_dynamodb_table_reachable():
    ddb = boto3.client("dynamodb", region_name=REGION)
    ddb.create_table(
        TableName="guardia-incidents",
        AttributeDefinitions=[{"AttributeName": "incident_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "incident_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )

    result = dependency_probe(DependencyProbeArgs(name="dynamodb:guardia-incidents"))

    assert result["kind"] == "dynamodb"
    assert result["reachable"] is True
    assert result["status"] == "ACTIVE"


@mock_aws
def test_dependency_probe_dynamodb_missing_table_is_unreachable():
    result = dependency_probe(DependencyProbeArgs(name="dynamodb:no-such-table"))
    assert result["reachable"] is False
    assert "error" in result


def test_dependency_probe_unknown_identifier():
    result = dependency_probe(DependencyProbeArgs(name="not-a-real-thing"))
    assert result["kind"] == "unknown"
    assert result["reachable"] is False


def test_dependency_probe_external_dependency_reports_latency():
    with patch("tools.read.dependency._probe_external") as mocked:
        mocked.return_value = {"reachable": True, "http_status": 200, "latency_ms": 42}
        result = dependency_probe(DependencyProbeArgs(name="mercadolibre-api"))

    assert result["kind"] == "external"
    assert result["reachable"] is True
    assert result["http_status"] == 200
