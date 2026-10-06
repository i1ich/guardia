"""Fault-injection harness (T15): labelled incidents with known ground truth.

Usage:
    python evals/fault_injection.py list
    python evals/fault_injection.py run <name|all> [--dry-run] [--out DIR] [--wait SECONDS]

Each injection breaks one disposable stub function from GuardiaInjectStack
(never PhotoList or LeaseLens), waits for its CloudWatch alarm, tears the
fault down, verifies the target is healthy again, and writes a labelled
incident JSON that validates against evals/schema/incident.schema.json.

Safety, in order:
  * the target function must carry the tag guardia-injectable=true AND a
    photolist-inject- name; otherwise the harness refuses before any change;
  * --dry-run lists every step and makes no AWS call at all;
  * teardown runs in a `finally`, so a failed wait still restores the target.

The harness runs with the operator's own AWS credentials, not a Guardia role:
the agent's roles hold no write access to these targets by design.

The stubs simulate failure signatures; they do not call MercadoLibre. So
"expire an ML token" means a stub rejecting an expired token value, not a
revoked real token.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = REPO_ROOT / "infrastructure" / "lambda" / "inject-target"
REGION = "sa-east-1"
TAG_KEY, TAG_VALUE = "guardia-injectable", "true"
NAME_PREFIX = "photolist-inject-"
TOKEN_PARAM = "/guardia-inject/token"
SEARCH_URL_PARAM = "/guardia-inject/search-url"
EXPIRED_TOKEN = "expired-token-v0"
BAD_SEARCH_URL = "https://api.mercadolibre.com/sites/MLU/search_v0"
HEALTHY_PAYLOAD = {"payload": "x" * 100}
OVERSIZE_PAYLOAD = {"payload": "x" * 8000}  # sleeps 8s against a 5s timeout


class Refused(Exception):
    """The harness declined to touch a resource."""


class Aws(Protocol):
    def function_tags(self, function: str) -> dict[str, str]: ...
    def get_param(self, name: str) -> str: ...
    def put_param(self, name: str, value: str) -> None: ...
    def update_code(self, function: str, zip_bytes: bytes) -> None: ...
    def get_concurrency(self, function: str) -> int | None: ...
    def put_concurrency(self, function: str, value: int) -> None: ...
    def delete_concurrency(self, function: str) -> None: ...
    def invoke(self, function: str, payload: dict) -> str | None:
        """Returns None on success, else the failure kind (function error or throttle)."""
    def alarm_state(self, alarm: str) -> str: ...
    def reset_alarm(self, alarm: str) -> None: ...


@dataclass
class Injection:
    name: str
    incident_class: str
    function: str
    alarm: str
    severity: str
    root_cause: str
    expected_top_3: list[str]
    signature: str  # what the logs/metrics show; used as the evidence excerpt
    plan_inject: list[str]
    plan_teardown: list[str]
    inject: Callable[[Aws, dict], None]
    teardown: Callable[[Aws, dict], None]
    check_clean: Callable[[Aws, dict], bool]
    traffic: Callable[[Aws], None]


def _zip(source: Path) -> bytes:
    """Deterministic zip of one file, stored as handler.py."""
    buf = io.BytesIO()
    info = zipfile.ZipInfo("handler.py", date_time=(2026, 1, 1, 0, 0, 0))
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(info, source.read_bytes())
    return buf.getvalue()


def _burst(function: str, payload: dict, times: int) -> Callable[[Aws], None]:
    def run(aws: Aws) -> None:
        for _ in range(times):
            aws.invoke(function, payload)

    return run


def _invoke_ok(function: str, aws: Aws) -> bool:
    return aws.invoke(function, HEALTHY_PAYLOAD) is None


def _param_injection(name, incident_class, function, alarm, param, bad, root_cause, top3, signature, severity):
    def inject(aws, saved):
        saved["param"] = aws.get_param(param)
        aws.put_param(param, bad)

    def teardown(aws, saved):
        aws.put_param(param, saved["param"])

    def clean(aws, saved):
        return aws.get_param(param) == saved["param"] and _invoke_ok(function, aws)

    return Injection(
        name, incident_class, function, alarm, severity, root_cause, top3, signature,
        [f"read SSM {param} (to restore later)", f"put SSM {param} = {bad!r}",
         f"invoke {function} x3 so Errors is emitted"],
        [f"put SSM {param} back to the saved value", f"invoke {function}; expect success"],
        inject, teardown, clean, _burst(function, HEALTHY_PAYLOAD, 3),
    )


def _build_injections() -> dict[str, Injection]:
    token_fn = NAME_PREFIX + "token-expiry"
    param_fn = NAME_PREFIX + "bad-param"
    deploy_fn = NAME_PREFIX + "bad-deploy"
    throttle_fn = NAME_PREFIX + "throttle"
    payload_fn = NAME_PREFIX + "payload"

    def deploy_inject(aws, saved):
        aws.update_code(deploy_fn, _zip(TARGET_DIR / "handler_bad.py"))

    def deploy_teardown(aws, saved):
        aws.update_code(deploy_fn, _zip(TARGET_DIR / "handler.py"))

    def throttle_inject(aws, saved):
        saved["concurrency"] = aws.get_concurrency(throttle_fn)
        aws.put_concurrency(throttle_fn, 0)

    def throttle_teardown(aws, saved):
        if saved.get("concurrency") is None:
            aws.delete_concurrency(throttle_fn)
        else:
            aws.put_concurrency(throttle_fn, saved["concurrency"])

    def throttle_clean(aws, saved):
        return aws.get_concurrency(throttle_fn) == saved.get("concurrency") and _invoke_ok(throttle_fn, aws)

    injections = [
        _param_injection(
            "token-expiry", "ml-token-expiry", token_fn, "photolist-inject-token-expiry-errors",
            TOKEN_PARAM, EXPIRED_TOKEN,
            "The stored MercadoLibre access token value expired and was not refreshed, "
            "so every call is rejected with 401 invalid_token.",
            ["Expired ML access token not refreshed (401 invalid_token)",
             "Refresh token itself revoked or expired",
             "MercadoLibre API outage"],
            "ERROR ML API 401 invalid_token: access token expired, refresh required", "high",
        ),
        _param_injection(
            "bad-param", "ml-api-403-search", param_fn, "photolist-inject-bad-param-errors",
            SEARCH_URL_PARAM, BAD_SEARCH_URL,
            "The SSM parameter holding the search endpoint was changed to a wrong URL, "
            "so MercadoLibre answers 403 Forbidden on every search.",
            ["SSM search-endpoint parameter changed to a wrong value",
             "MercadoLibre app lost search access (certification or scope)",
             "Expired access token"],
            "ERROR ML search returned 403 Forbidden for configured endpoint", "medium",
        ),
        Injection(
            "bad-deploy", "deploy-regression", deploy_fn, "photolist-inject-bad-deploy-errors", "high",
            "A new code version was deployed whose handler raises on every invocation "
            "('NoneType' object has no attribute 'get').",
            ["Bad code deployed to the function (AttributeError on every call)",
             "Misconfigured environment variable after deploy",
             "Downstream dependency returning None"],
            "ERROR handler failed after deploy: 'NoneType' object has no attribute 'get'",
            [f"upload handler_bad.py as the code of {deploy_fn}", f"invoke {deploy_fn} x3 so Errors is emitted"],
            [f"upload the healthy handler.py to {deploy_fn}", f"invoke {deploy_fn}; expect success"],
            deploy_inject, deploy_teardown, lambda aws, saved: _invoke_ok(deploy_fn, aws),
            _burst(deploy_fn, HEALTHY_PAYLOAD, 3),
        ),
        Injection(
            "throttle", "cost-throttling-anomaly", throttle_fn, "photolist-inject-throttle-throttles", "medium",
            "Reserved concurrency of the function was set to 0, so the Lambda service "
            "rejects every invocation as throttled.",
            ["Reserved concurrency set to 0 (all invocations throttled)",
             "Account-level concurrency exhausted by another function",
             "Traffic spike beyond provisioned concurrency"],
            "Throttles > 0 with no function logs: invocations rejected before the code ran",
            [f"put reserved concurrency 0 on {throttle_fn}", f"invoke {throttle_fn} x3 so Throttles is emitted"],
            [f"remove (or restore) reserved concurrency on {throttle_fn}", f"invoke {throttle_fn}; expect success"],
            throttle_inject, throttle_teardown, throttle_clean, _burst(throttle_fn, HEALTHY_PAYLOAD, 3),
        ),
        Injection(
            "payload", "lambda-timeout-cold-start", payload_fn, "photolist-inject-payload-duration", "high",
            "Invocations carry a payload whose processing time (8s) exceeds the function's "
            "5s timeout, so each one is killed by the timeout.",
            ["Oversized input makes processing exceed the 5s function timeout",
             "Cold start plus slow dependency",
             "Function memory too low"],
            "Task timed out after 5.00 seconds",
            [f"invoke {payload_fn} x2 with an 8000-char payload (8s of work vs 5s timeout)"],
            ["nothing to restore (no state was changed)", f"invoke {payload_fn} with a small payload; expect success"],
            lambda aws, saved: None, lambda aws, saved: None,
            lambda aws, saved: _invoke_ok(payload_fn, aws),
            _burst(payload_fn, OVERSIZE_PAYLOAD, 2),
        ),
    ]
    return {i.name: i for i in injections}


INJECTIONS = _build_injections()


def guard(aws: Aws, injection: Injection) -> None:
    if not injection.function.startswith(NAME_PREFIX):
        raise Refused(f"{injection.function}: name does not start with {NAME_PREFIX}")
    tags = aws.function_tags(injection.function)
    if tags.get(TAG_KEY) != TAG_VALUE:
        raise Refused(f"{injection.function}: missing tag {TAG_KEY}={TAG_VALUE}; refusing to touch it")


def label(injection: Injection, started: datetime, ended: datetime) -> dict:
    return {
        "incident_id": f"photolist-inject-{injection.name}-{started:%Y-%m-%d}",
        "source_system": "photolist-latam",
        "alarm_name": injection.alarm,
        "timestamp": f"{started:%Y-%m-%dT%H:%M:%SZ}",
        "incident_class": injection.incident_class,
        "severity": injection.severity,
        "evidence": [
            {
                "type": "log",
                "ref": f"logs:/aws/lambda/{injection.function}@{started:%Y-%m-%dT%H:%M:%SZ}/{ended:%Y-%m-%dT%H:%M:%SZ}",
                "excerpt": injection.signature,
            }
        ],
        "ground_truth_root_cause": injection.root_cause,
        "expected_top_3": injection.expected_top_3,
        "notes": "origin: fault-injection on a disposable stub (synthetic by construction). "
        "Intake dedupes by alarm name, so the real incident id differs; match on alarm_name and timestamp.",
    }


def run_one(aws: Aws, injection: Injection, out_dir: Path, wait_seconds: int,
            sleep: Callable[[float], None] = time.sleep, log=print) -> dict:
    guard(aws, injection)
    aws.reset_alarm(injection.alarm)  # a clean OK -> ALARM transition is what notifies
    saved: dict = {}
    started = datetime.now(timezone.utc)
    alarm_fired = False
    try:
        log(f"[{injection.name}] inject")
        injection.inject(aws, saved)
        injection.traffic(aws)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if aws.alarm_state(injection.alarm) == "ALARM":
                alarm_fired = True
                break
            sleep(10)
        log(f"[{injection.name}] alarm {'fired' if alarm_fired else 'DID NOT FIRE'}")
    finally:
        log(f"[{injection.name}] teardown")
        injection.teardown(aws, saved)
    ended = datetime.now(timezone.utc)
    clean = injection.check_clean(aws, saved)
    log(f"[{injection.name}] target clean: {clean}")
    record = label(injection, started, ended)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{record['incident_id']}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"name": injection.name, "alarm_fired": alarm_fired, "clean": clean, "label": str(path)}


def dry_run(injection: Injection, log=print) -> None:
    log(f"[{injection.name}] class={injection.incident_class} target={injection.function} alarm={injection.alarm}")
    log("  guard:    function tag guardia-injectable=true and photolist-inject- name")
    log(f"  reset:    alarm {injection.alarm} to OK")
    for step in injection.plan_inject:
        log(f"  inject:   {step}")
    log("  wait:     for the alarm to reach ALARM")
    for step in injection.plan_teardown:
        log(f"  teardown: {step}")


class BotoAws:
    def __init__(self):
        import boto3

        self.lam = boto3.client("lambda", region_name=REGION)
        self.ssm = boto3.client("ssm", region_name=REGION)
        self.cw = boto3.client("cloudwatch", region_name=REGION)

    def function_tags(self, function):
        arn = self.lam.get_function(FunctionName=function)["Configuration"]["FunctionArn"]
        return self.lam.list_tags(Resource=arn)["Tags"]

    def get_param(self, name):
        return self.ssm.get_parameter(Name=name)["Parameter"]["Value"]

    def put_param(self, name, value):
        self.ssm.put_parameter(Name=name, Value=value, Overwrite=True)

    def update_code(self, function, zip_bytes):
        self.lam.update_function_code(FunctionName=function, ZipFile=zip_bytes)
        self.lam.get_waiter("function_updated_v2").wait(FunctionName=function)

    def get_concurrency(self, function):
        return self.lam.get_function_concurrency(FunctionName=function).get("ReservedConcurrentExecutions")

    def put_concurrency(self, function, value):
        self.lam.put_function_concurrency(FunctionName=function, ReservedConcurrentExecutions=value)

    def delete_concurrency(self, function):
        self.lam.delete_function_concurrency(FunctionName=function)

    def invoke(self, function, payload):
        try:
            resp = self.lam.invoke(FunctionName=function, Payload=json.dumps(payload).encode())
        except self.lam.exceptions.TooManyRequestsException:
            return "throttled"
        return resp.get("FunctionError")

    def alarm_state(self, alarm):
        alarms = self.cw.describe_alarms(AlarmNames=[alarm])["MetricAlarms"]
        return alarms[0]["StateValue"] if alarms else "MISSING"

    def reset_alarm(self, alarm):
        self.cw.set_alarm_state(AlarmName=alarm, StateValue="OK", StateReason="guardia fault-injection reset")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("name", choices=[*INJECTIONS, "all"])
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--out", type=Path, default=Path("injected-incidents"))
    run.add_argument("--wait", type=int, default=300, help="seconds to wait for the alarm per injection")
    args = parser.parse_args(argv)

    if args.command == "list":
        for i in INJECTIONS.values():
            print(f"{i.name:14} {i.incident_class:26} {i.function}")
        return 0

    chosen = list(INJECTIONS.values()) if args.name == "all" else [INJECTIONS[args.name]]
    if args.dry_run:
        for injection in chosen:
            dry_run(injection)
        return 0

    aws = BotoAws()
    failed = False
    for injection in chosen:
        try:
            result = run_one(aws, injection, args.out, args.wait)
        except Refused as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        failed |= not (result["alarm_fired"] and result["clean"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
