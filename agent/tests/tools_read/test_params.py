import inspect

import boto3
from moto import mock_aws

import tools.read.params as params_module
from tools.read._common import REGION
from tools.read.params import ParamMetadataArgs, param_metadata


@mock_aws
def test_param_metadata_never_returns_a_value_for_a_known_secret():
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(Name="/photolist/ml/client_secret", Value="super-secret-value", Type="String")

    result = param_metadata(ParamMetadataArgs(name="/photolist/ml/client_secret"))

    assert result["found"] is True
    assert result["type"] == "String"
    assert "value" not in {k.lower() for k in result}
    assert "super-secret-value" not in str(result)


@mock_aws
def test_param_metadata_reports_not_found_for_unknown_parameter():
    result = param_metadata(ParamMetadataArgs(name="/photolist/does-not-exist"))
    assert result["found"] is False


def test_param_metadata_module_never_calls_get_parameter_or_history():
    source = inspect.getsource(params_module)
    assert "get_parameter(" not in source
    assert "get_parameter_history" not in source
