import boto3
from moto import mock_aws

from tools.read._common import REGION
from tools.read.deployments import RecentDeploymentsArgs, recent_deployments

MINIMAL_TEMPLATE = """
{
  "Resources": {
    "Bucket": {"Type": "AWS::S3::Bucket"}
  }
}
"""


@mock_aws
def test_recent_deployments_lists_events_for_an_existing_stack():
    cfn = boto3.client("cloudformation", region_name=REGION)
    cfn.create_stack(StackName="PhotolistApiStack", TemplateBody=MINIMAL_TEMPLATE)

    result = recent_deployments(RecentDeploymentsArgs(stack="PhotolistApiStack", window_minutes=180))

    assert result["stack"] == "PhotolistApiStack"
    assert isinstance(result["events"], list)
    assert result["truncated"] is False
    if result["events"]:
        event = result["events"][0]
        assert set(event) == {"timestamp", "logical_resource_id", "resource_type", "status", "status_reason"}


@mock_aws
def test_recent_deployments_reports_error_for_unknown_stack():
    result = recent_deployments(RecentDeploymentsArgs(stack="NoSuchStack", window_minutes=60))
    assert result["events"] == []
    assert "error" in result
