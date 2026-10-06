"""Disposable fault-injection target (T15).

One stub Lambda per injection kind (env INJECT_KIND). It performs no real
work and calls no external service: it reads a config value and fails with
the log signature a real incident of that class leaves behind. The harness
(evals/fault_injection.py) flips config, code, concurrency or payload size
and the failure follows by construction.
"""

import os
import time

import boto3

HEALTHY_SEARCH_URL = "https://api.mercadolibre.com/sites/MLU/search"


def _param(name_env: str) -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=os.environ[name_env])["Parameter"]["Value"]


def handler(event, context):
    kind = os.environ["INJECT_KIND"]

    if kind == "token-expiry":
        token = _param("TOKEN_PARAM")
        if token.startswith("expired"):
            print("ERROR ML API 401 invalid_token: access token expired, refresh required")
            raise RuntimeError("MercadoLibre rejected the access token (401 invalid_token)")

    elif kind == "bad-param":
        url = _param("SEARCH_URL_PARAM")
        if url != HEALTHY_SEARCH_URL:
            print(f"ERROR ML search returned 403 Forbidden for configured endpoint {url}")
            raise RuntimeError("MercadoLibre search returned 403")

    elif kind == "payload":
        # Work scales with payload size; the harness sends one past the timeout.
        time.sleep(len(event.get("payload", "")) / 1000)

    # "bad-deploy" and "throttle" are healthy here: the first fails only once
    # the harness uploads handler_bad.py, the second fails at the Lambda
    # service (reserved concurrency 0) before this code runs.
    print(f"OK kind={kind}")
    return {"ok": True, "kind": kind}
