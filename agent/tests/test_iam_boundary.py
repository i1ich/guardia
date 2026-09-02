"""T8 validation: assuming guardia-read-role and calling a mutating API
must return AccessDenied. Live only -- needs the deployed GuardiaIamStack
and real AWS credentials able to sts:AssumeRole into it.
"""

import os

import boto3
import pytest
from botocore.exceptions import ClientError

pytestmark = pytest.mark.skipif(
    os.environ.get("GUARDIA_LIVE_AWS_TESTS") != "1",
    reason="set GUARDIA_LIVE_AWS_TESTS=1 to run against the deployed GuardiaIamStack",
)

REGION = "sa-east-1"
READ_ROLE_ARN = "arn:aws:iam::727990090900:role/guardia-read-role"


def _assume_read_role_session() -> boto3.Session:
    sts = boto3.client("sts", region_name=REGION)
    creds = sts.assume_role(RoleArn=READ_ROLE_ARN, RoleSessionName="guardia-t8-access-denied-check")["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=REGION,
    )


def test_read_role_can_do_its_actual_job():
    """Sanity check the assumed session isn't just broadly denied -- it can
    do a real read-only call the role is supposed to grant."""
    session = _assume_read_role_session()
    result = session.client("ssm").describe_parameters(
        ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": ["/photolist/ml/client_secret"]}]
    )
    assert result["Parameters"], "read role could not perform its own granted DescribeParameters action"


def test_read_role_is_denied_a_mutating_ssm_call():
    session = _assume_read_role_session()
    with pytest.raises(ClientError) as exc_info:
        session.client("ssm").put_parameter(
            Name="/guardia-test/t8-access-denied-check", Value="probe", Type="String"
        )
    # SSM/Lambda use "AccessDeniedException"; other services (e.g. S3) use
    # the plain "AccessDenied" code -- both mean the same implicit-deny outcome.
    assert exc_info.value.response["Error"]["Code"] in ("AccessDenied", "AccessDeniedException")


def test_read_role_is_denied_a_delete_call():
    session = _assume_read_role_session()
    with pytest.raises(ClientError) as exc_info:
        session.client("lambda").delete_function(FunctionName="guardia-t8-nonexistent-probe-function")
    # SSM/Lambda use "AccessDeniedException"; other services (e.g. S3) use
    # the plain "AccessDenied" code -- both mean the same implicit-deny outcome.
    assert exc_info.value.response["Error"]["Code"] in ("AccessDenied", "AccessDeniedException")
