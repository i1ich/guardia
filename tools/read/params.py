"""param_metadata — SSM parameter metadata only. Never a value.

Deliberately built on ssm:DescribeParameters alone. GetParameter and
GetParameterHistory are never called from this module — GetParameterHistory
in particular would return the plaintext historical value for a
String/StringList parameter, which would defeat the point. The IAM read
role (T8, infrastructure/lib/iam-stack.ts) does not grant either action,
so this is a belt-and-suspenders guarantee: even a code change here
could not smuggle a value through the deployed role.
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._common import client


class ParamMetadataArgs(BaseModel):
    name: str = Field(..., description="Exact SSM parameter name, e.g. '/photolist/ml/client_secret'.")


def param_metadata(args: ParamMetadataArgs) -> dict:
    ssm = client("ssm")
    response = ssm.describe_parameters(
        ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [args.name]}]
    )
    matches = response.get("Parameters", [])
    if not matches:
        return {"name": args.name, "found": False}

    param = matches[0]
    return {
        "name": param["Name"],
        "found": True,
        "type": param.get("Type"),
        "tier": param.get("Tier"),
        "version": param.get("Version"),
        "last_modified": param["LastModifiedDate"].isoformat() if param.get("LastModifiedDate") else None,
        "last_modified_user": param.get("LastModifiedUser"),
    }


@tool("param_metadata", args_schema=ParamMetadataArgs)
def param_metadata_tool(name: str) -> dict:
    """Look up SSM parameter metadata (existence, type, version, last-modified) — never the value."""
    return param_metadata(ParamMetadataArgs(name=name))
