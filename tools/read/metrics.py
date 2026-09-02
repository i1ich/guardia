"""get_metrics — CloudWatch metric datapoints for one metric over a bounded window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._common import MAX_METRIC_DATAPOINTS, client


class GetMetricsArgs(BaseModel):
    namespace: str = Field(..., description="CloudWatch namespace, e.g. 'AWS/Lambda'.")
    metric_name: str = Field(..., description="Metric name, e.g. 'Duration', 'Errors', 'Throttles'.")
    dimensions: dict[str, str] = Field(
        default_factory=dict, description="Dimension name/value pairs, e.g. {'FunctionName': 'photolist-analyze-photo'}."
    )
    window_minutes: int = Field(60, ge=1, le=10080, description="How far back to fetch, in minutes (max 7 days).")
    period_seconds: int = Field(300, ge=60, description="Datapoint granularity in seconds.")
    stat: str = Field("Average", description="Statistic: Average, Sum, Maximum, Minimum, SampleCount.")


def get_metrics(args: GetMetricsArgs) -> dict:
    cw = client("cloudwatch")
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=args.window_minutes)

    response = cw.get_metric_data(
        MetricDataQueries=[
            {
                "Id": "m1",
                "MetricStat": {
                    "Metric": {
                        "Namespace": args.namespace,
                        "MetricName": args.metric_name,
                        "Dimensions": [{"Name": k, "Value": v} for k, v in args.dimensions.items()],
                    },
                    "Period": args.period_seconds,
                    "Stat": args.stat,
                },
                "ReturnData": True,
            }
        ],
        StartTime=start_time,
        EndTime=end_time,
    )

    result = response["MetricDataResults"][0] if response["MetricDataResults"] else {"Timestamps": [], "Values": []}
    timestamps = result.get("Timestamps", [])
    values = result.get("Values", [])
    datapoints = [
        {"timestamp": ts.isoformat(), "value": val} for ts, val in zip(timestamps, values)
    ]
    datapoints.sort(key=lambda d: d["timestamp"])

    truncated = len(datapoints) > MAX_METRIC_DATAPOINTS
    if truncated:
        datapoints = datapoints[-MAX_METRIC_DATAPOINTS:]

    return {
        "namespace": args.namespace,
        "metric_name": args.metric_name,
        "dimensions": args.dimensions,
        "stat": args.stat,
        "period_seconds": args.period_seconds,
        "datapoints": datapoints,
        "truncated": truncated,
    }


@tool("get_metrics", args_schema=GetMetricsArgs)
def get_metrics_tool(
    namespace: str,
    metric_name: str,
    dimensions: dict[str, str] | None = None,
    window_minutes: int = 60,
    period_seconds: int = 300,
    stat: str = "Average",
) -> dict:
    """Fetch CloudWatch metric datapoints for one metric over a bounded window."""
    return get_metrics(
        GetMetricsArgs(
            namespace=namespace,
            metric_name=metric_name,
            dimensions=dimensions or {},
            window_minutes=window_minutes,
            period_seconds=period_seconds,
            stat=stat,
        )
    )
