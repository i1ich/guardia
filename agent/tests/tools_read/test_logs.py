import time

import boto3
from moto import mock_aws

from tools.read._common import MAX_TOTAL_LOG_CHARS, REGION
from tools.read.logs import QueryLogsArgs, query_logs


@mock_aws
def test_query_logs_returns_bounded_lines_for_existing_log_group():
    logs = boto3.client("logs", region_name=REGION)
    log_group = "/aws/lambda/photolist-analyze-photo"
    logs.create_log_group(logGroupName=log_group)
    logs.create_log_stream(logGroupName=log_group, logStreamName="stream-1")
    now_ms = int(time.time() * 1000)
    logs.put_log_events(
        logGroupName=log_group,
        logStreamName="stream-1",
        logEvents=[
            {"timestamp": now_ms, "message": "INFO handling request 1"},
            {"timestamp": now_ms, "message": "ERROR timeout talking to dependency"},
        ],
    )

    result = query_logs(QueryLogsArgs(function="photolist-analyze-photo", window_minutes=15))

    assert result["function"] == "photolist-analyze-photo"
    assert result["log_group"] == log_group
    assert isinstance(result["lines"], list)
    # moto's Logs Insights emulation is best-effort; what we assert is the
    # tool's own contract, not moto's query engine: it must not blow the
    # bound and must return a well-formed shape.
    total_chars = sum(len(line["message"]) for line in result["lines"])
    assert total_chars <= MAX_TOTAL_LOG_CHARS


@mock_aws
def test_query_logs_handles_missing_log_group_without_raising():
    result = query_logs(QueryLogsArgs(function="does-not-exist", window_minutes=15))
    assert result["lines"] == []
    assert "error" in result


def test_query_logs_strips_binary_payloads_from_returned_lines():
    from tools.read._common import strip_binary_payloads

    blob = "A" * 500
    line = f"uploaded image: {blob} done"
    cleaned = strip_binary_payloads(line)
    assert blob not in cleaned
    assert "[binary-payload-omitted]" in cleaned
    assert "uploaded image:" in cleaned and "done" in cleaned
