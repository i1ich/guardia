"""The seven read-only tools (T6): everything the agent needs to reconstruct
incident context, and nothing more. Each is exposed as a plain function
(for direct testing) and as a LangChain tool (the `*_tool` object, for
binding into the graph).
"""

from tools.read.dependency import DependencyProbeArgs, dependency_probe, dependency_probe_tool
from tools.read.deployments import RecentDeploymentsArgs, recent_deployments, recent_deployments_tool
from tools.read.logs import QueryLogsArgs, query_logs, query_logs_tool
from tools.read.metrics import GetMetricsArgs, get_metrics, get_metrics_tool
from tools.read.params import ParamMetadataArgs, param_metadata, param_metadata_tool
from tools.read.resources import StackResourcesArgs, stack_resources, stack_resources_tool
from tools.read.runbook import SearchRunbookArgs, search_runbook, search_runbook_tool

ALL_READ_TOOLS = [
    query_logs_tool,
    get_metrics_tool,
    recent_deployments_tool,
    stack_resources_tool,
    param_metadata_tool,
    dependency_probe_tool,
    search_runbook_tool,
]

__all__ = [
    "ALL_READ_TOOLS",
    "DependencyProbeArgs",
    "GetMetricsArgs",
    "ParamMetadataArgs",
    "QueryLogsArgs",
    "RecentDeploymentsArgs",
    "SearchRunbookArgs",
    "StackResourcesArgs",
    "dependency_probe",
    "dependency_probe_tool",
    "get_metrics",
    "get_metrics_tool",
    "param_metadata",
    "param_metadata_tool",
    "query_logs",
    "query_logs_tool",
    "recent_deployments",
    "recent_deployments_tool",
    "search_runbook",
    "search_runbook_tool",
    "stack_resources",
    "stack_resources_tool",
]
