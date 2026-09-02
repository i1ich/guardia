from tools.read.runbook import SearchRunbookArgs, search_runbook


def test_search_runbook_stub_returns_empty_results_not_a_guess():
    result = search_runbook(SearchRunbookArgs(query="mercadolibre 403 on search"))
    assert result["results"] == []
    assert result["status"] == "not_implemented"
