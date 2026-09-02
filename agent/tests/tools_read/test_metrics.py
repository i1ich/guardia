from datetime import datetime, timedelta, timezone

import boto3
from moto import mock_aws

from tools.read._common import REGION
from tools.read.metrics import GetMetricsArgs, get_metrics


@mock_aws
def test_get_metrics_returns_datapoints_for_a_published_metric():
    cw = boto3.client("cloudwatch", region_name=REGION)
    now = datetime.now(timezone.utc)
    cw.put_metric_data(
        Namespace="AWS/Lambda",
        MetricData=[
            {
                "MetricName": "Errors",
                "Dimensions": [{"Name": "FunctionName", "Value": "photolist-analyze-photo"}],
                "Timestamp": now - timedelta(minutes=5),
                "Value": 3.0,
            }
        ],
    )

    result = get_metrics(
        GetMetricsArgs(
            namespace="AWS/Lambda",
            metric_name="Errors",
            dimensions={"FunctionName": "photolist-analyze-photo"},
            window_minutes=30,
            stat="Sum",
        )
    )

    assert result["namespace"] == "AWS/Lambda"
    assert result["metric_name"] == "Errors"
    assert result["truncated"] is False
    assert isinstance(result["datapoints"], list)


@mock_aws
def test_get_metrics_returns_empty_list_for_unpublished_metric():
    result = get_metrics(
        GetMetricsArgs(namespace="AWS/Lambda", metric_name="NoSuchMetric", dimensions={}, window_minutes=15)
    )
    assert result["datapoints"] == []
