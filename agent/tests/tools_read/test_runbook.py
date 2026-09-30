import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from guardia_agent.plan import PLANNERS, _Ctx
from tools.read import _bm25
from tools.read._common import REGION
from tools.read.runbook import RunbookRetriever, SearchRunbookArgs, reset_index_cache, search_runbook

RUNBOOKS = [
    {
        "name": "token-renewal",
        "description": "Renew an expired OAuth access token with the refresh token grant.",
        "aliases": ["access token expired", "401 invalid_token"],
        "body": "# Renew\n\nPOST the refresh_token to the oauth endpoint.\n\n## Verify\n\nCall /users/me and expect 200.",
    },
    {
        "name": "deploy-procedure",
        "description": "Deploy the stack with cdk and run the smoke tests.",
        "aliases": ["deploy regression", "redeploy the stack"],
        "body": "# Deploy\n\nRun cdk deploy, then the smoke tests.\n\n## Rollback\n\nRedeploy the previous commit.",
    },
    {
        "name": "shell-sessions",
        "description": "Keep an AWS CloudShell session alive.",
        "aliases": ["cloudshell session timed out"],
        "body": "# Sessions\n\nVariables are lost when the tab reloads.",
    },
]
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    reset_index_cache()
    for var in ("GUARDIA_RUNBOOK_INDEX_PATH", "GUARDIA_RUNBOOKS_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    yield
    reset_index_cache()


@pytest.fixture
def index():
    return _bm25.build_index(RUNBOOKS)


def names(hits):
    return [h["name"] for h in hits]


def test_tokenizer_folds_accents_and_stems_consistently():
    assert _bm25.tokenize("Certificación") == _bm25.tokenize("certificacion")
    assert _bm25.tokenize("deployed deploying deploys") == ["deploy", "deploy", "deploy"]
    assert "the" not in _bm25.tokenize("the stack")


def test_relevant_runbook_ranks_first(index):
    assert names(_bm25.search(index, "the access token expired"))[0] == "token-renewal"
    assert names(_bm25.search(index, "redeploy the stack with cdk"))[0] == "deploy-procedure"


def test_aliases_lift_a_runbook_even_when_the_body_never_says_it(index):
    assert "cloudshell session timed out" not in RUNBOOKS[2]["body"]
    assert names(_bm25.search(index, "cloudshell session timed out")) == ["shell-sessions"]


def test_unrelated_query_returns_nothing_not_the_nearest_runbook(index):
    assert _bm25.search(index, "what is the capital of Uruguay") == []
    assert _bm25.search(index, "") == []


def test_unknown_words_count_against_a_match(index):
    # "token" matches a runbook, but four words the corpus has never seen
    # dominate the query: not confident enough to return anything.
    assert _bm25.search(index, "token zebra quasar nebula fjord") == []


def test_excerpt_is_the_best_matching_section(index):
    hit = _bm25.search(index, "rollback redeploy previous commit")[0]
    assert hit["name"] == "deploy-procedure"
    assert hit["section"] == "Rollback"
    assert "previous commit" in hit["excerpt"]


def test_unsupported_index_version_is_rejected(index):
    with pytest.raises(ValueError):
        _bm25.search({**index, "version": 99}, "token")


def test_retriever_returns_langchain_documents(index):
    docs = RunbookRetriever(index=index).invoke("access token expired")
    assert docs and docs[0].metadata["name"] == "token-renewal"
    assert docs[0].page_content


def test_search_runbook_reports_index_unavailable_instead_of_raising():
    result = search_runbook(SearchRunbookArgs(query="access token expired"))
    assert result["status"] == "index_unavailable"
    assert result["results"] == []


def test_search_runbook_from_a_local_index_file(tmp_path, monkeypatch, index):
    path = tmp_path / "bm25.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setenv("GUARDIA_RUNBOOK_INDEX_PATH", str(path))

    hit = search_runbook(SearchRunbookArgs(query="access token expired"))
    assert hit["status"] == "ok" and hit["results"][0]["name"] == "token-renewal"

    miss = search_runbook(SearchRunbookArgs(query="what is the capital of Uruguay"))
    assert miss["status"] == "no_match" and miss["results"] == []


@mock_aws
def test_search_runbook_loads_the_index_from_s3(monkeypatch, index):
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="guardia-runbooks-test", CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_object(Bucket="guardia-runbooks-test", Key="index/bm25.json", Body=json.dumps(index).encode())
    monkeypatch.setenv("GUARDIA_RUNBOOKS_BUCKET", "guardia-runbooks-test")

    result = search_runbook(SearchRunbookArgs(query="redeploy the stack"))
    assert result["status"] == "ok" and result["results"][0]["name"] == "deploy-procedure"


def test_every_runbook_query_the_planner_emits_is_in_the_retrieval_eval():
    """T9 -> T17 contract: the queries plan.py really sends are the ones measured."""
    envelope = {
        "source_system": "photolist-latam",
        "timestamp": "2026-09-29T15:41:00Z",
        "metric": {"name": "Errors", "dimensions": {"FunctionName": "photolist-analyze-photo"}},
    }
    emitted = {
        step["args"]["query"]
        for planner in PLANNERS.values()
        for step in planner(_Ctx({"envelope": envelope, "now_epoch": 1_790_696_460.0}))
        if step["tool"] == "search_runbook"
    }
    measured = {q["query"] for q in json.loads((REPO_ROOT / "evals" / "retrieval_queries.json").read_text(encoding="utf-8"))}
    assert emitted <= measured, f"unmeasured planner queries: {emitted - measured}"
