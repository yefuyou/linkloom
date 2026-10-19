import pytest

from tests.m3i_support import product_case, finish
from tests.m3m_support import context_for
from tests.unit.test_m3o_regex_scope import READ_CASES


@pytest.mark.parametrize('query',[READ_CASES[0],READ_CASES[-1]])
def test_explicit_task_domain_reaches_existing_chain_with_original_question(tmp_path,query):
    app,service,ask,stages,workspace = product_case(tmp_path)
    try:
        result,status = ask.ask(dict(query=query,workspace_id=workspace,
            investigation_context=context_for(app,workspace,'tasks')))
        assert status == 202,result
        result = finish(ask,workspace,result)
        assert result['reviewer']['accepted'],result
        assert result['preparation']['question'] == query
        assert [len(s.requests) for s in stages] == [2,1,1]
        assert not result['conclusions']
    finally:
        app.close()
