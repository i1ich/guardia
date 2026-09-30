"""search_runbook — retrieve the operator's own procedure for an incident (T17).

The agent retrieves procedures a human already trusts instead of
improvising a fix. Retrieval is BM25 over the runbooks plus hand-written
`aliases` (see _bm25.py for why it is not embeddings), exposed as a
LangChain retriever so a hybrid/embedding retriever can later be combined
with it via EnsembleRetriever without touching call sites.

The index is built offline (scripts/build_runbook_index.py) and read from
S3 at `index/bm25.json` (or a local file via GUARDIA_RUNBOOK_INDEX_PATH for
development). A missing index is reported as a status, never raised: one
unavailable tool must not abort an incident.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.read._bm25 import search
from tools.read._common import client

INDEX_KEY = "index/bm25.json"
MAX_RESULTS = 3

_cached_index: dict[str, Any] | None = None


class SearchRunbookArgs(BaseModel):
    query: str = Field(..., description="Natural-language description of the incident to find a runbook for.")


def _load_index() -> dict[str, Any]:
    global _cached_index
    if _cached_index is not None:
        return _cached_index
    local = os.environ.get("GUARDIA_RUNBOOK_INDEX_PATH")
    if local:
        _cached_index = json.loads(Path(local).read_text(encoding="utf-8"))
        return _cached_index
    bucket = os.environ.get("GUARDIA_RUNBOOKS_BUCKET")
    if not bucket:
        raise FileNotFoundError("neither GUARDIA_RUNBOOK_INDEX_PATH nor GUARDIA_RUNBOOKS_BUCKET is set")
    body = client("s3").get_object(Bucket=bucket, Key=INDEX_KEY)["Body"].read()
    _cached_index = json.loads(body)
    return _cached_index


def reset_index_cache() -> None:
    global _cached_index
    _cached_index = None


class RunbookRetriever(BaseRetriever):
    """BM25 runbook retriever; returns [] rather than the nearest irrelevant runbook."""

    index: dict[str, Any]
    k: int = MAX_RESULTS

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun | None = None) -> list[Document]:
        return [
            Document(
                page_content=hit["excerpt"] or hit["description"],
                metadata={key: hit[key] for key in ("name", "score", "coverage", "matched_terms", "description", "section")},
            )
            for hit in search(self.index, query, k=self.k)
        ]


def search_runbook(args: SearchRunbookArgs) -> dict:
    try:
        index = _load_index()
    except (OSError, ValueError, ClientError, BotoCoreError) as exc:
        return {"query": args.query, "results": [], "status": "index_unavailable", "error": str(exc)}

    docs = RunbookRetriever(index=index).invoke(args.query)
    results = [{**doc.metadata, "excerpt": doc.page_content} for doc in docs]
    return {"query": args.query, "results": results, "status": "ok" if results else "no_match"}


@tool("search_runbook", args_schema=SearchRunbookArgs)
def search_runbook_tool(query: str) -> dict:
    """Retrieve the operator's own runbook most relevant to a described incident; empty if none applies."""
    return search_runbook(SearchRunbookArgs(query=query))
