"""stack_resources — the physical resources belonging to a CloudFormation stack."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._common import MAX_STACK_RESOURCES, client


class StackResourcesArgs(BaseModel):
    stack: str = Field(..., description="CloudFormation stack name, e.g. 'PhotolistApiStack'.")


def stack_resources(args: StackResourcesArgs) -> dict:
    cfn = client("cloudformation")
    paginator = cfn.get_paginator("list_stack_resources")

    resources: list[dict] = []
    truncated = False
    for page in paginator.paginate(StackName=args.stack):
        for res in page["StackResourceSummaries"]:
            resources.append(
                {
                    "logical_id": res.get("LogicalResourceId"),
                    "physical_id": res.get("PhysicalResourceId"),
                    "resource_type": res.get("ResourceType"),
                    "status": res.get("ResourceStatus"),
                    "last_updated": res["LastUpdatedTimestamp"].isoformat() if res.get("LastUpdatedTimestamp") else None,
                }
            )
            if len(resources) >= MAX_STACK_RESOURCES:
                truncated = True
                break
        if truncated:
            break

    return {"stack": args.stack, "resources": resources, "truncated": truncated}


@tool("stack_resources", args_schema=StackResourcesArgs)
def stack_resources_tool(stack: str) -> dict:
    """List the physical resources belonging to a CloudFormation stack."""
    return stack_resources(StackResourcesArgs(stack=stack))
