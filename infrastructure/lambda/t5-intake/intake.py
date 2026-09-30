"""T5 alarm intake: CloudWatch alarm -> SNS -> this Lambda.

Normalizes an alarm notification into the incident envelope, opens exactly
one incident per alarm per dedup window, and joins later state changes of
the same alarm to that incident instead of opening a second one.

Deliberately stdlib + boto3 only, so the Lambda ships as a plain asset with
no vendored dependencies (unlike the T4 spike).

Starting the graph run is NOT wired yet: the graph (T9/T10) and its Lambda
do not exist. A newly opened incident is written with graph_status
"pending_graph"; T9 replaces that with an async invoke of the graph Lambda
(mode "start", thread_id = incident_id).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

# alarm-name prefix -> source_system (values match incident.schema.json)
SUBJECT_PREFIXES = {
    "photolist-": "photolist-latam",
    "leaselens-": "lease-lens",
}
SOURCE_SHORT = {"photolist-latam": "photolist", "lease-lens": "leaselens"}

DEFAULT_DEDUP_WINDOW_SECONDS = 900
_TTL_GRACE_SECONDS = 86400  # dedup rows are deleted a day after their window closes


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _parse_time(raw: str) -> datetime:
    # CloudWatch sends e.g. "2026-09-29T15:41:00.000+0000"
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def source_system_for(alarm_name: str, dimensions: dict[str, str]) -> str | None:
    lowered = alarm_name.lower()
    for prefix, system in SUBJECT_PREFIXES.items():
        if lowered.startswith(prefix):
            return system
    function_name = dimensions.get("FunctionName", "").lower()
    for prefix, system in SUBJECT_PREFIXES.items():
        if function_name.startswith(prefix):
            return system
    return None


def normalize(message: dict[str, Any]) -> dict[str, Any] | None:
    """CloudWatch alarm SNS payload -> incident envelope, or None if the alarm
    does not belong to a subject system (e.g. Guardia's own alarms)."""
    alarm_name = message["AlarmName"]
    trigger = message.get("Trigger") or {}
    dimensions = {d["name"]: d["value"] for d in trigger.get("Dimensions", []) if "name" in d}
    source = source_system_for(alarm_name, dimensions)
    if source is None:
        return None

    changed_at = _parse_time(message["StateChangeTime"])
    period = trigger.get("Period")
    evaluation_periods = trigger.get("EvaluationPeriods")
    window = period * evaluation_periods if period and evaluation_periods else None
    return {
        "source_system": source,
        "alarm_name": alarm_name,
        "timestamp": _iso(changed_at),
        "metric": {
            "namespace": trigger.get("Namespace"),
            "name": trigger.get("MetricName"),
            "statistic": trigger.get("Statistic"),
            "dimensions": dimensions,
        },
        "threshold": trigger.get("Threshold"),
        "comparison": trigger.get("ComparisonOperator"),
        "window_seconds": window,
        "reason": message.get("NewStateReason", ""),
    }


def _incident_id(envelope: dict[str, Any]) -> str:
    stamp = envelope["timestamp"].replace("-", "").replace(":", "").lower()
    return f"{SOURCE_SHORT[envelope['source_system']]}-{_slug(envelope['alarm_name'])}-{stamp}"


def _is_conditional_failure(error: ClientError) -> bool:
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _dynamo_safe(value: Any) -> Any:
    # DynamoDB's resource API rejects Python floats (thresholds arrive as 1.0).
    return json.loads(json.dumps(value), parse_float=Decimal)


def _open_incident(incidents, incident_id: str, envelope: dict[str, Any], event_key: str) -> None:
    incidents.put_item(
        Item={
            "incident_id": incident_id,
            **_dynamo_safe(envelope),
            "status": "open",
            "graph_status": "pending_graph",
            "events": [{"state_change_time": envelope["timestamp"], "reason": envelope["reason"]}],
            "event_keys": {event_key},
            "opened_at": envelope["timestamp"],
        },
        ConditionExpression="attribute_not_exists(incident_id)",
    )


def process_alarm(
    message: dict[str, Any],
    *,
    incidents,
    checkpoints,
    now: float | None = None,
    window_seconds: int = DEFAULT_DEDUP_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Returns {"action": opened|joined|duplicate|ignored, "incident_id"?, "reason"?}."""
    now = time.time() if now is None else now

    if message.get("NewStateValue") != "ALARM":
        return {"action": "ignored", "reason": f"state {message.get('NewStateValue')} does not open or join"}
    envelope = normalize(message)
    if envelope is None:
        return {"action": "ignored", "reason": "not a subject-system alarm"}

    alarm_name = envelope["alarm_name"]
    event_key = f"{alarm_name}|{message['StateChangeTime']}"
    new_id = _incident_id(envelope)
    dedup_key = {"PK": f"DEDUP#{alarm_name}", "SK": "open_incident"}
    expires = int(now) + window_seconds

    # Atomic claim: exactly one concurrent delivery wins the right to open.
    try:
        checkpoints.put_item(
            Item={**dedup_key, "incident_id": new_id, "window_expires_at": expires, "ttl": expires + _TTL_GRACE_SECONDS},
            ConditionExpression="attribute_not_exists(PK) OR window_expires_at < :now",
            ExpressionAttributeValues={":now": int(now)},
        )
    except ClientError as error:
        if not _is_conditional_failure(error):
            raise
    else:
        _open_incident(incidents, new_id, envelope, event_key)
        return {"action": "opened", "incident_id": new_id}

    # Someone already holds the window: join their incident.
    existing_id = checkpoints.get_item(Key=dedup_key, ConsistentRead=True)["Item"]["incident_id"]
    try:
        incidents.update_item(
            Key={"incident_id": existing_id},
            UpdateExpression="SET events = list_append(events, :ev) ADD event_keys :ks",
            ConditionExpression="attribute_exists(incident_id) AND NOT contains(event_keys, :k)",
            ExpressionAttributeValues={
                ":ev": [{"state_change_time": envelope["timestamp"], "reason": envelope["reason"]}],
                ":ks": {event_key},
                ":k": event_key,
            },
        )
    except ClientError as error:
        if not _is_conditional_failure(error):
            raise
        if "Item" not in incidents.get_item(Key={"incident_id": existing_id}, ConsistentRead=True):
            # The claimer crashed between the dedup write and the incident write.
            _open_incident(incidents, existing_id, envelope, event_key)
            return {"action": "opened", "incident_id": existing_id, "reason": "recovered orphaned dedup claim"}
        return {"action": "duplicate", "incident_id": existing_id, "reason": "event already recorded"}

    # A flapping alarm keeps its incident open: slide the window forward.
    checkpoints.update_item(
        Key=dedup_key,
        UpdateExpression="SET window_expires_at = :e, #t = :t",
        ExpressionAttributeNames={"#t": "ttl"},
        ExpressionAttributeValues={":e": expires, ":t": expires + _TTL_GRACE_SECONDS},
    )
    return {"action": "joined", "incident_id": existing_id}


def handler(event: dict, context: Any) -> list[dict[str, Any]]:
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "sa-east-1"))
    incidents = dynamodb.Table(os.environ["GUARDIA_INCIDENTS_TABLE"])
    checkpoints = dynamodb.Table(os.environ["GUARDIA_CHECKPOINTS_TABLE"])
    window = int(os.environ.get("GUARDIA_DEDUP_WINDOW_SECONDS", DEFAULT_DEDUP_WINDOW_SECONDS))

    results = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["Sns"]["Message"])
            if not isinstance(message, dict) or "AlarmName" not in message:
                raise ValueError("not a CloudWatch alarm notification")
        except (KeyError, ValueError) as error:
            results.append({"action": "ignored", "reason": f"unparseable message: {error}"})
            continue
        results.append(
            process_alarm(message, incidents=incidents, checkpoints=checkpoints, window_seconds=window)
        )
    print(json.dumps(results, default=str))
    return results
