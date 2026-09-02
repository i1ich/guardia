import boto3
from moto import mock_aws

from tools.read._common import REGION
from tools.read.resources import StackResourcesArgs, stack_resources

MINIMAL_TEMPLATE = """
{
  "Resources": {
    "Bucket": {"Type": "AWS::S3::Bucket"}
  }
}
"""


@mock_aws
def test_stack_resources_lists_resources_for_an_existing_stack():
    cfn = boto3.client("cloudformation", region_name=REGION)
    cfn.create_stack(StackName="PhotolistApiStack", TemplateBody=MINIMAL_TEMPLATE)

    result = stack_resources(StackResourcesArgs(stack="PhotolistApiStack"))

    assert result["stack"] == "PhotolistApiStack"
    assert result["truncated"] is False
    assert any(r["resource_type"] == "AWS::S3::Bucket" for r in result["resources"])
