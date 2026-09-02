"""query_logs — CloudWatch Logs Insights over a single Lambda function's log group.

Field allow-list: the Insights query only ever selects @timestamp and
@message. @message itself is then bounded (tools._common) so an embedded
image or document payload cannot reach the model — this is the log-query
half of T6's "scoped field allow-list" requirement; secret redaction is a
separate, later concern (T7).
"""

from __future__ import annotations

import time

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._common import (
    MAX_LOG_LINES,
    MAX_TOTAL_LOG_CHARS,
    bound_line,
    client,
)

_POLL_INTERVAL_SECONDS = 1.0
_QUERY_TIMEOUT_SECONDS = 30.0


class QueryLogsArgs(BaseModel):
    function: str = Field(..., description="Lambda function name, e.g. 'photolist-analyze-photo'.")
    window_minutes: int = Field(15, ge=1, le=1440, description="How far back to search, in minutes.")
    filter: str | None = Field(
        None,
        description="Optional CloudWatch Logs Insights filter expression, e.g. 'ERROR' or 'like /timeout/i'.",
    )


def query_logs(args: QueryLogsArgs) -> dict:
    logs = client("logs")
    log_group = f"/aws/lambda/{args.function}"
    end_time = int(time.time())
    start_time = end_time - args.window_minutes * 60

    query = "fields @timestamp, @message"
    if args.filter:
        query += f" | filter {args.filter}"
    query += f" | sort @timestamp desc | limit {MAX_LOG_LINES}"

    try:
        start = logs.start_query(
            logGroupName=log_group,
            startTime=start_time,
            endTime=end_time,
            queryString=query,
        )
    except logs.exceptions.ResourceNotFoundException:
        return {"function": args.function, "log_group": log_group, "lines": [], "truncated": False, "error": "log group not found"}

    query_id = start["queryId"]
    deadline = time.monotonic() + _QUERY_TIMEOUT_SECONDS
    result: dict = {"status": "Running"}
    while time.monotonic() < deadline:
        result = logs.get_query_results(queryId=query_id)
        if result["status"] in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(_POLL_INTERVAL_SECONDS)

    lines: list[dict] = []
    total_chars = 0
    for row in result.get("results", []):
        fields = {f["field"]: f["value"] for f in row}
        message = bound_line(fields.get("@message", ""))
        if total_chars + len(message) > MAX_TOTAL_LOG_CHARS:
            break
        total_chars += len(message)
        lines.append({"timestamp": fields.get("@timestamp"), "message": message})

    return {
        "function": args.function,
        "log_group": log_group,
        "window_minutes": args.window_minutes,
        "lines": lines,
        "truncated": len(lines) < len(result.get("results", [])),
        "query_status": result.get("status", "Unknown"),
    }


@tool("query_logs", args_schema=QueryLogsArgs)
def query_logs_tool(function: str, window_minutes: int = 15, filter: str | None = None) -> dict:
    """Search a Lambda function's recent logs via CloudWatch Logs Insights."""
    return query_logs(QueryLogsArgs(function=function, window_minutes=window_minutes, filter=filter))
