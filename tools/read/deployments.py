"""recent_deployments — CloudFormation stack events within a bounded recent window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._common import MAX_STACK_EVENTS, bound_line, client


class RecentDeploymentsArgs(BaseModel):
    stack: str = Field(..., description="CloudFormation stack name, e.g. 'PhotolistApiStack'.")
    window_minutes: int = Field(180, ge=1, le=10080, description="How far back to search, in minutes.")


def recent_deployments(args: RecentDeploymentsArgs) -> dict:
    cfn = client("cloudformation")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.window_minutes)

    try:
        paginator = cfn.get_paginator("describe_stack_events")
        events: list[dict] = []
        truncated = False
        for page in paginator.paginate(StackName=args.stack):
            for event in page["StackEvents"]:
                if event["Timestamp"] < cutoff:
                    break
                events.append(
                    {
                        "timestamp": event["Timestamp"].isoformat(),
                        "logical_resource_id": event.get("LogicalResourceId"),
                        "resource_type": event.get("ResourceType"),
                        "status": event.get("ResourceStatus"),
                        "status_reason": bound_line(event.get("ResourceStatusReason", "")),
                    }
                )
                if len(events) >= MAX_STACK_EVENTS:
                    truncated = True
                    break
            else:
                continue
            break
    except ClientError as exc:
        return {"stack": args.stack, "events": [], "truncated": False, "error": str(exc)}

    return {
        "stack": args.stack,
        "window_minutes": args.window_minutes,
        "events": events,
        "truncated": truncated,
    }


@tool("recent_deployments", args_schema=RecentDeploymentsArgs)
def recent_deployments_tool(stack: str, window_minutes: int = 180) -> dict:
    """List recent CloudFormation stack events for a stack, within a bounded window."""
    return recent_deployments(RecentDeploymentsArgs(stack=stack, window_minutes=window_minutes))
