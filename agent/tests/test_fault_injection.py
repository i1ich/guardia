import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from evals import fault_injection as fi

SCHEMA = json.loads((Path(fi.__file__).parent / "schema" / "incident.schema.json").read_text())


class FakeAws:
    """In-memory stand-in that reproduces the stubs' behaviour."""

    def __init__(self, tagged=True, alarm_fires=True):
        self.tagged = tagged
        self.alarm_fires = alarm_fires
        self.params = {fi.TOKEN_PARAM: "valid-token-v1", fi.SEARCH_URL_PARAM: "https://api.mercadolibre.com/sites/MLU/search"}
        self.code = {}
        self.concurrency = {}
        self.calls = []
        self.alarm = "OK"

    def _rec(self, *a):
        self.calls.append(a)

    def function_tags(self, function):
        self._rec("tags", function)
        return {fi.TAG_KEY: fi.TAG_VALUE} if self.tagged else {}

    def get_param(self, name):
        return self.params[name]

    def put_param(self, name, value):
        self._rec("put_param", name)
        self.params[name] = value

    def update_code(self, function, zip_bytes):
        self._rec("update_code", function)
        self.code[function] = zip_bytes

    def get_concurrency(self, function):
        return self.concurrency.get(function)

    def put_concurrency(self, function, value):
        self._rec("put_concurrency", function)
        self.concurrency[function] = value

    def delete_concurrency(self, function):
        self._rec("delete_concurrency", function)
        self.concurrency.pop(function, None)

    def invoke(self, function, payload):
        self._rec("invoke", function)
        kind = function.removeprefix(fi.NAME_PREFIX)
        if self.concurrency.get(function) == 0:
            return "throttled"
        if kind == "token-expiry" and self.params[fi.TOKEN_PARAM].startswith("expired"):
            return "Unhandled"
        if kind == "bad-param" and not self.params[fi.SEARCH_URL_PARAM].endswith("/search"):
            return "Unhandled"
        if kind == "bad-deploy" and self.code.get(function) == fi._zip(fi.TARGET_DIR / "handler_bad.py"):
            return "Unhandled"
        if kind == "payload" and len(payload.get("payload", "")) > 5000:
            return "Unhandled"
        return None

    def alarm_state(self, alarm):
        return "ALARM" if self.alarm_fires else "OK"

    def reset_alarm(self, alarm):
        self._rec("reset_alarm", alarm)


def run(aws, injection, tmp_path, wait=1):
    return fi.run_one(aws, injection, tmp_path, wait, sleep=lambda s: None, log=lambda m: None)


def test_five_injections_cover_all_five_classes():
    assert len(fi.INJECTIONS) == 5
    assert {i.incident_class for i in fi.INJECTIONS.values()} == {
        "ml-api-403-search", "ml-token-expiry", "deploy-regression",
        "lambda-timeout-cold-start", "cost-throttling-anomaly",
    }


@pytest.mark.parametrize("name", list(fi.INJECTIONS))
def test_injection_breaks_then_fully_tears_down(name, tmp_path):
    injection = fi.INJECTIONS[name]
    aws = FakeAws()
    baseline_params = dict(aws.params)

    # Prove the injection actually breaks the target mid-run.
    saved = {}
    injection.inject(aws, saved)
    if name == "payload":  # the fault is the traffic, not stored state
        assert aws.invoke(injection.function, fi.OVERSIZE_PAYLOAD) is not None
    else:
        assert fi._invoke_ok(injection.function, aws) is False
    injection.teardown(aws, saved)
    assert fi._invoke_ok(injection.function, aws) is True
    assert aws.params == baseline_params
    assert aws.concurrency.get(injection.function) is None

    result = run(aws, injection, tmp_path)
    assert result["alarm_fired"] and result["clean"]


@pytest.mark.parametrize("name", list(fi.INJECTIONS))
def test_label_validates_against_schema(name, tmp_path):
    result = run(FakeAws(), fi.INJECTIONS[name], tmp_path)
    record = json.loads(Path(result["label"]).read_text())
    assert list(Draft202012Validator(SCHEMA).iter_errors(record)) == []
    assert record["incident_class"] == fi.INJECTIONS[name].incident_class


def test_refuses_untagged_resource_before_any_change(tmp_path):
    aws = FakeAws(tagged=False)
    with pytest.raises(fi.Refused):
        run(aws, fi.INJECTIONS["bad-param"], tmp_path)
    assert [c[0] for c in aws.calls] == ["tags"]


def test_refuses_a_function_outside_the_inject_namespace(tmp_path):
    real = fi.INJECTIONS["bad-param"]
    other = fi.Injection(**{**real.__dict__, "function": "photolist-analyze-photo"})
    aws = FakeAws()
    with pytest.raises(fi.Refused):
        run(aws, other, tmp_path)
    assert aws.calls == []


def test_teardown_runs_when_the_wait_fails(tmp_path):
    aws = FakeAws()
    aws.alarm_state = lambda alarm: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        run(aws, fi.INJECTIONS["token-expiry"], tmp_path)
    assert aws.params[fi.TOKEN_PARAM] == "valid-token-v1"


def test_alarm_that_never_fires_is_reported(tmp_path):
    result = run(FakeAws(alarm_fires=False), fi.INJECTIONS["throttle"], tmp_path, wait=0)
    assert result["alarm_fired"] is False and result["clean"] is True


def test_dry_run_lists_steps_and_never_touches_aws(monkeypatch, capsys):
    def boom():
        raise AssertionError("dry-run must not create an AWS client")

    monkeypatch.setattr(fi, "BotoAws", boom)
    assert fi.main(["run", "all", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for name in fi.INJECTIONS:
        assert f"[{name}]" in out
    assert "inject:" in out and "teardown:" in out
