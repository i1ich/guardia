import boto3
import pytest
from moto import mock_aws

from guardia_agent import model


@mock_aws
def test_missing_key_raises_a_clear_error():
    ssm = boto3.client("ssm", region_name="sa-east-1")
    ssm.put_parameter(Name=model.MODEL_PARAM, Value="claude-sonnet-5", Type="String")
    with pytest.raises(model.ModelNotConfigured, match="anthropic-api-key"):
        model.build_chat_model(ssm)


@mock_aws
def test_builds_a_bounded_model_without_tools():
    ssm = boto3.client("ssm", region_name="sa-east-1")
    ssm.put_parameter(Name=model.MODEL_PARAM, Value="claude-sonnet-5", Type="String")
    ssm.put_parameter(Name=model.API_KEY_PARAM, Value="sk-ant-test-not-real", Type="SecureString")
    chat = model.build_chat_model(ssm)
    assert chat.model == "claude-sonnet-5"
    assert chat.max_tokens == model.MAX_TOKENS_PER_CALL
    assert "sk-ant-test-not-real" not in repr(chat)
