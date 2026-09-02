"""search_runbook — stub. T17 wires this to the real S3-backed embedding index.

Kept here now (rather than left undeclared) so T10's gather/hypothesize
loop can be built and tested against a stable schema today and rewired
onto the real retriever later without touching call sites.
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class SearchRunbookArgs(BaseModel):
    query: str = Field(..., description="Natural-language description of the incident to find a runbook for.")


def search_runbook(args: SearchRunbookArgs) -> dict:
    return {"query": args.query, "results": [], "status": "not_implemented", "note": "T17 wires the real retriever here."}


@tool("search_runbook", args_schema=SearchRunbookArgs)
def search_runbook_tool(query: str) -> dict:
    """Retrieve the runbook most relevant to a described incident. Stub until T17."""
    return search_runbook(SearchRunbookArgs(query=query))
