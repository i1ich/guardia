"""dependency_probe — lightweight reachability/status check for a declared dependency.

Two kinds of dependency: an AWS resource the subject systems rely on
(a DynamoDB table, SQS queue, SNS topic, or another Lambda function —
identified as "<kind>:<name>"), or an external HTTP dependency identified
by name (currently just "mercadolibre-api", a public, unauthenticated
health endpoint — no credential ever needs to leave this process).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from botocore.exceptions import ClientError
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._common import client

_HTTP_TIMEOUT_SECONDS = 5.0

_EXTERNAL_DEPENDENCIES = {
    "mercadolibre-api": "https://api.mercadolibre.com/sites/MLU",
}


class DependencyProbeArgs(BaseModel):
    name: str = Field(
        ...,
        description=(
            "Dependency identifier: 'dynamodb:<table>', 'sqs:<queue-name>', 'sns:<topic-name>', "
            "'lambda:<function>', or a known external dependency name (e.g. 'mercadolibre-api')."
        ),
    )


def _probe_dynamodb(table_name: str) -> dict:
    try:
        resp = client("dynamodb").describe_table(TableName=table_name)
        return {"reachable": True, "status": resp["Table"]["TableStatus"]}
    except ClientError as exc:
        return {"reachable": False, "error": str(exc)}


def _probe_sqs(queue_name: str) -> dict:
    sqs = client("sqs")
    try:
        url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
        return {"reachable": True, "approximate_messages": attrs["Attributes"].get("ApproximateNumberOfMessages")}
    except ClientError as exc:
        return {"reachable": False, "error": str(exc)}


def _probe_sns(topic_arn_or_name: str) -> dict:
    try:
        resp = client("sns").get_topic_attributes(TopicArn=topic_arn_or_name)
        return {"reachable": True, "subscriptions_confirmed": resp["Attributes"].get("SubscriptionsConfirmed")}
    except ClientError as exc:
        return {"reachable": False, "error": str(exc)}


def _probe_lambda(function_name: str) -> dict:
    try:
        resp = client("lambda").get_function(FunctionName=function_name)
        return {"reachable": True, "state": resp["Configuration"].get("State")}
    except ClientError as exc:
        return {"reachable": False, "error": str(exc)}


_AWS_PROBES = {
    "dynamodb": _probe_dynamodb,
    "sqs": _probe_sqs,
    "sns": _probe_sns,
    "lambda": _probe_lambda,
}


def _probe_external(url: str) -> dict:
    # GET, not HEAD: several third-party APIs (MercadoLibre's included)
    # reject HEAD outright regardless of headers, which would misreport a
    # perfectly healthy dependency as unreachable.
    start = time.monotonic()
    try:
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "guardia-agent/0.1"})
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"reachable": True, "http_status": response.status, "latency_ms": latency_ms}
    except urllib.error.HTTPError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"reachable": True, "http_status": exc.code, "latency_ms": latency_ms}
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"reachable": False, "error": str(exc)}


def dependency_probe(args: DependencyProbeArgs) -> dict:
    if args.name in _EXTERNAL_DEPENDENCIES:
        result = _probe_external(_EXTERNAL_DEPENDENCIES[args.name])
        return {"name": args.name, "kind": "external", **result}

    if ":" in args.name:
        kind, target = args.name.split(":", 1)
        probe = _AWS_PROBES.get(kind)
        if probe is not None:
            result = probe(target)
            return {"name": args.name, "kind": kind, **result}

    return {"name": args.name, "kind": "unknown", "reachable": False, "error": "unrecognized dependency identifier"}


@tool("dependency_probe", args_schema=DependencyProbeArgs)
def dependency_probe_tool(name: str) -> dict:
    """Check reachability/status of a declared dependency (AWS resource or known external service)."""
    return dependency_probe(DependencyProbeArgs(name=name))
