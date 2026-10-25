# LinkLoom product walkthrough / 产品演示指南

Use synthetic notes and a dedicated demo database. Earlier screenshots under
`docs/assets/v1/` show the previous copy; they remain unchanged as historical evidence.

## Task investigation — offline

1. Follow the README command with `--offline-task-demo` and a dedicated database.
2. Open **Ask**, choose tasks, and ask whether the meetings describe the same task.
3. Read the record count and **Partial results** / **Insufficient information** status.
4. Check **Offline demo**, the **AI proposals · awaiting confirmation** label, and the source cards.
5. Expand **Field evidence and original message** to compare each interpreted field with its quote.
6. Read the outstanding questions and next steps. Task relationships and the effective plan remain unconfirmed.
7. Switch the answer language and resize to a phone width. These operations do not authorize a new model call.

This mode uses scripted responses with public synthetic sources. It demonstrates
product behavior; it does not measure real-model understanding. Task relationship
approval is unavailable in this UI.

## Sources → Review → Decision Memory → Ask

This separate workflow needs AI extraction. Follow [README setup](../README.md#quickstart)
and review the external-source transmission notice before importing. External AI
can incur fees; new investigations also require per-question permission.

1. Create a workspace and import `tests/fixtures/v1_release_acceptance_meeting.txt`.
   Choose **Import and extract**. If AI is unavailable, **Save source** retains
   the text for later extraction.
2. In **Review**, open Birchline. Check the subject, relationship, original quote
   and effective date. Fill unresolved fields only from the source.
3. Enter your reviewer name and reason, then **Approve decision**.
   Approval stays unavailable while the relationship is unresolved.
4. Review and approve Wrenwell against its replacement wording and October 8
   effective date. Kestrel Ledger stays an **Unapproved proposal**.
5. In **Decision Memory**, inspect Wrenwell, its predecessor and the original
   quotes. Approval and source version details are expandable.
6. In **Ask**, ask “What supplier are we currently using?” Expect the reviewed
   Wrenwell record. Use October 7 in advanced options to inspect Birchline.
   Historical results use approvals recorded today.
7. Update the source through **Sources**. Affected decisions may appear in
   **Decision records needing attention**. Open **Revalidate decision**, compare
   current evidence, and give any replacement a separate approval.

Inspect actual extraction output before approving; model output may vary.

## 中文操作

离线任务演示：使用 README 中的 `--offline-task-demo` 命令及独立演示数据库。
在“提问”中选择任务调查，先看记录数与“部分结果／资料不足”状态，再看来源原文。
AI 提案保持“待确认”；字段证据、关系核验过程与运行详情可展开查看。
“离线演示”始终可见，不代表真实模型能力已通过评测。当前界面暂不支持任务关系审批。

完整决策流程：在“来源”导入合成会议，进入“审核”核对原文、生效日期、对象和关系，
填写审核人与理由后选择“批准决策”。在“决策记忆”查看当前记录与历史，
在“提问”查询当前或指定日期的决策。来源变化后，进入“待复核决策”重新核验；
新的替代记录需要独立批准。外部 AI 的内容发送与费用授权在提交位置展示。
