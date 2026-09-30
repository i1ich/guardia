import json
import re
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infrastructure" / "lambda" / "t5-intake"))
import intake  # noqa: E402

REGION = "sa-east-1"
NOW = 1_800_000_000.0


def alarm_message(name="photolist-analyze-photo-errors", state="ALARM", when="2026-09-29T15:41:00.000+0000"):
    return {
        "AlarmName": name,
        "NewStateValue": state,
        "NewStateReason": "Threshold Crossed: 1 datapoint [3.0] was >= the threshold (1.0).",
        "StateChangeTime": when,
        "Region": "South America (Sao Paulo)",
        "Trigger": {
            "MetricName": "Errors",
            "Namespace": "AWS/Lambda",
            "Statistic": "SUM",
            "Dimensions": [{"name": "FunctionName", "value": "photolist-analyze-photo"}],
            "Period": 300,
            "EvaluationPeriods": 1,
            "Threshold": 1.0,
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        },
    }


@pytest.fixture
def tables():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        incidents = dynamodb.create_table(
            TableName="guardia-incidents",
            KeySchema=[{"AttributeName": "incident_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "incident_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        checkpoints = dynamodb.create_table(
            TableName="guardia-checkpoints",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield incidents, checkpoints


def run(tables, message, now=NOW, window=900):
    incidents, checkpoints = tables
    return intake.process_alarm(message, incidents=incidents, checkpoints=checkpoints, now=now, window_seconds=window)


def test_normalize_builds_the_envelope():
    envelope = intake.normalize(alarm_message())
    assert envelope["source_system"] == "photolist-latam"
    assert envelope["alarm_name"] == "photolist-analyze-photo-errors"
    assert envelope["timestamp"] == "2026-09-29T15:41:00Z"
    assert envelope["metric"] == {
        "namespace": "AWS/Lambda",
        "name": "Errors",
        "statistic": "SUM",
        "dimensions": {"FunctionName": "photolist-analyze-photo"},
    }
    assert envelope["threshold"] == 1.0
    assert envelope["window_seconds"] == 300


def test_incident_id_matches_the_corpus_slug_pattern():
    incident_id = intake._incident_id(intake.normalize(alarm_message()))
    assert re.fullmatch(r"[a-z0-9-]+", incident_id)  # incident.schema.json pattern
    assert incident_id == "photolist-photolist-analyze-photo-errors-20260929t154100z"


def test_first_alarm_opens_exactly_one_incident(tables):
    incidents, _ = tables
    result = run(tables, alarm_message())
    assert result["action"] == "opened"
    items = incidents.scan()["Items"]
    assert len(items) == 1
    assert items[0]["incident_id"] == result["incident_id"]
    assert items[0]["graph_status"] == "pending_graph"
    assert items[0]["source_system"] == "photolist-latam"


def test_second_state_change_60s_later_joins_instead_of_opening(tables):
    incidents, _ = tables
    first = run(tables, alarm_message(when="2026-09-29T15:41:00.000+0000"), now=NOW)
    second = run(tables, alarm_message(when="2026-09-29T15:42:00.000+0000"), now=NOW + 60)

    assert second == {"action": "joined", "incident_id": first["incident_id"]}
    items = incidents.scan()["Items"]
    assert len(items) == 1
    assert [e["state_change_time"] for e in items[0]["events"]] == [
        "2026-09-29T15:41:00Z",
        "2026-09-29T15:42:00Z",
    ]


def test_sns_redelivery_of_the_same_event_is_a_noop(tables):
    incidents, _ = tables
    message = alarm_message()
    run(tables, message)
    again = run(tables, message, now=NOW + 5)
    assert again["action"] == "duplicate"
    assert len(incidents.scan()["Items"][0]["events"]) == 1


def test_alarm_after_the_window_opens_a_new_incident(tables):
    incidents, _ = tables
    first = run(tables, alarm_message(when="2026-09-29T15:41:00.000+0000"), now=NOW, window=900)
    later = run(tables, alarm_message(when="2026-09-29T16:41:00.000+0000"), now=NOW + 3600, window=900)
    assert later["action"] == "opened"
    assert later["incident_id"] != first["incident_id"]
    assert len(incidents.scan()["Items"]) == 2


def test_joining_slides_the_window_so_a_flapping_alarm_keeps_one_incident(tables):
    incidents, _ = tables
    first = run(tables, alarm_message(when="2026-09-29T15:41:00.000+0000"), now=NOW, window=900)
    run(tables, alarm_message(when="2026-09-29T15:50:00.000+0000"), now=NOW + 600, window=900)
    third = run(tables, alarm_message(when="2026-09-29T16:00:00.000+0000"), now=NOW + 1200, window=900)
    assert third == {"action": "joined", "incident_id": first["incident_id"]}


def test_different_alarms_get_separate_incidents(tables):
    incidents, _ = tables
    run(tables, alarm_message(name="photolist-analyze-photo-errors"))
    run(tables, alarm_message(name="photolist-analyze-photo-throttles"))
    assert len(incidents.scan()["Items"]) == 2


def test_ok_and_insufficient_data_do_not_open_incidents(tables):
    incidents, _ = tables
    assert run(tables, alarm_message(state="OK"))["action"] == "ignored"
    assert run(tables, alarm_message(state="INSUFFICIENT_DATA"))["action"] == "ignored"
    assert incidents.scan()["Items"] == []


def test_non_subject_alarm_is_ignored(tables):
    incidents, _ = tables
    message = alarm_message(name="guardia-intake-errors")
    message["Trigger"]["Dimensions"] = [{"name": "FunctionName", "value": "guardia-intake"}]
    assert run(tables, message)["action"] == "ignored"
    assert incidents.scan()["Items"] == []


def test_source_falls_back_to_the_function_dimension():
    message = alarm_message(name="high-latency-alarm")
    message["Trigger"]["Dimensions"] = [{"name": "FunctionName", "value": "leaselens-analyze-worker"}]
    assert intake.normalize(message)["source_system"] == "lease-lens"


def test_orphaned_dedup_claim_is_recovered_by_the_retry(tables):
    incidents, checkpoints = tables
    # The claimer wrote the dedup row and crashed before writing the incident.
    checkpoints.put_item(
        Item={
            "PK": "DEDUP#photolist-analyze-photo-errors",
            "SK": "open_incident",
            "incident_id": "photolist-orphan",
            "window_expires_at": int(NOW) + 900,
        }
    )
    result = run(tables, alarm_message(), now=NOW + 10)
    assert result == {
        "action": "opened",
        "incident_id": "photolist-orphan",
        "reason": "recovered orphaned dedup claim",
    }
    assert incidents.get_item(Key={"incident_id": "photolist-orphan"})["Item"]["status"] == "open"


def test_handler_parses_sns_records_and_survives_junk(tables, monkeypatch):
    monkeypatch.setenv("GUARDIA_INCIDENTS_TABLE", "guardia-incidents")
    monkeypatch.setenv("GUARDIA_CHECKPOINTS_TABLE", "guardia-checkpoints")
    monkeypatch.setenv("AWS_REGION", REGION)
    incidents, _ = tables
    event = {
        "Records": [
            {"Sns": {"Message": json.dumps(alarm_message())}},
            {"Sns": {"Message": "not json at all"}},
            {"Sns": {"Message": json.dumps({"hello": "world"})}},
        ]
    }
    results = intake.handler(event, None)
    assert [r["action"] for r in results] == ["opened", "ignored", "ignored"]
    assert len(incidents.scan()["Items"]) == 1
