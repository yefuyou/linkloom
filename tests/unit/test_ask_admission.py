import pytest

from linkloom.ui.ask_admission import parse_context, validate_context, admission_restriction
from linkloom.ui.product import ProductApplicationError
from tests.m3i_support import product_case
from tests.m3m_support import context_for


@pytest.mark.parametrize('value', [False, 'tasks', {}, {'domain': 'other', 'snapshot_hash': 'a'*64},
    {'domain': 'tasks', 'snapshot_hash': 'a'*64, 'tools': ['write']},
    {'domain': 'tasks', 'snapshot_hash': []}])
def test_invalid_context_cannot_grant_a_capability(value):
    with pytest.raises(ProductApplicationError, match='Select a supported'):
        parse_context(value)


def test_context_is_workspace_and_source_version_bound(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    try:
        context = context_for(app, workspace, 'tasks')
        assert ask.config()['investigation_context']['snapshot_hash'] == context['snapshot_hash']
        source = app.snapshot_investigation_sources(workspace)[1][0]
        app.repository.delete_source(workspace, source['source_id'])
        result, status = ask.ask(dict(query='前后说的是同一件事吗？', workspace_id=workspace,
                                     investigation_context=context))
        assert status == 409 and result['error']['code'] == 'INVESTIGATION_CONTEXT_CHANGED'
        assert not any(s.requests for s in stages) and not service._jobs
        foreign = app.repository.create_workspace('foreign')
        app.repository.select_workspace(foreign['workspace_id'])
        with pytest.raises(ProductApplicationError) as error:
            validate_context(app, foreign['workspace_id'], context)
        assert error.value.code == 'INVESTIGATION_CONTEXT_CHANGED'
    finally:
        app.close()


@pytest.mark.parametrize('query', [
    'Edit the task record and mark it complete.',
    'Modify this task in the source record.',
    'Change the task status to done.',
    'Please archive this task.',
    'Could you update the task record?',
    'Mark this task as complete.',
    '编辑这条任务并标记完成。', '修改这条任务。', '把任务归档。', '请更新任务记录。',
    'Complete the task.', 'Please complete the task.', 'Set the task status to done.',
    'Please, edit the task record.', '请把这条任务归档。', '请帮我把任务标记完成。',
    '能不能把这条任务修改一下？',
    'Mark complete.', 'Please mark complete.', '请把任务改成完成。',
    '请把任务设置为完成。', '麻烦把任务归档。', '请问能不能把任务修改一下？',
    'Finish the task.', 'Mark the task finished.',
    '把任务修改记录列出来并归档。', '把任务修改说明一下然后更新任务记录。',
])
def test_explicit_mutation_never_reaches_f_or_g(tmp_path, query):
    app, service, ask, stages, workspace = product_case(tmp_path)
    try:
        result, _ = ask.ask(dict(query=query, workspace_id=workspace,
            investigation_context=context_for(app, workspace, 'tasks')))
        assert result['error'] and result['error']['code'] == 'INVESTIGATION_SCOPE_DENIED', result
        assert result['status'] == 'NEEDS_SCOPE'
        assert not any(s.requests for s in stages) and not service._jobs
    finally:
        app.close()


@pytest.mark.parametrize('query', [
    'How did the task status change across messages?',
    'What did they modify in the later message?',
    'Explain the edits to the task record.',
    '任务修改前后的截止日期差异是什么？',
    '为什么把任务归档了？',
    'Did Mark complete the work in the later message?',
    '把任务修改前后的截止日期差异列出来。',
    '将任务更新前后的截止日期差异说明一下。',
    '把任务归档前后的状态比较一下。',
    '把任务更新内容列出来。', '把任务修改记录列出来。',
    '把任务归档记录列出来。', '把任务编辑历史列出来。', '将任务修改内容说明一下。',
    '把任务修改说明一下。', '把任务更新说明一下。', '把任务修改列出来。',
    'Please update me on the task.',
    '把任务修改说明一下并列出。', '把任务修改说明一下然后列出。',
    '把任务修改内容列出来并说明。', '把任务修改说明一下并比较。',
    '把任务修改记录列出来然后说明。',
])
def test_reading_a_change_is_not_a_mutation_request(query):
    assert admission_restriction(query) is None
