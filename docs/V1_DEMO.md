# LinkLoom V1 demo walkthrough

This 60–90 second walkthrough follows the V1 product UI from a meeting
artifact to a stale decision alert. Allow extra time for provider latency.

## Before the demo

The Sources → Review → Decision Memory → Ask workflow needs semantic
extraction. The local UI disables extraction by default. Install the existing
Google GenAI SDK extra if needed, configure GEMINI_API_KEY in a shell secret
manager, and start LinkLoom with the explicit external-provider option described
in the [README Quickstart](../README.md#quickstart).

The fixture is synthetic:
tests/fixtures/v1_release_acceptance_meeting.txt

Import it through Sources. Do not insert records through SQLite or call private
domain functions. The product UI sends the source text to Gemini only after the
explicit launch-time opt-in.

For a key-free preview, /legacy-ask replays an older deterministic decision
brief; it does not demonstrate the V1 workflow. The screenshots in the README
were captured with a deterministic fake provider and are not a public provider
switch.

## Walkthrough

1. **Open Sources.** Create or select a demo workspace. The source and its
   decision history stay scoped to that workspace.

2. **Import the meeting.** Select the synthetic fixture in the Sources file
   picker and choose **Import and extract**. Wait for **COMPLETE**. The
   acceptance fixture produced three segments, two decision candidates for
   review, and one supporting proposal.

3. **Inspect the first candidate.** Open Review and select **Birchline**.
   Confirm its type is DECISION, its subject and relation, the exact October 6
   evidence quote, the source name, and the effective date. If the UI shows
   “Not resolved”, enter 2026-10-06 based on that quote.

4. **Approve Birchline.** Enter a reviewer and a short reason, then choose
   **Approve and materialize**. The Review UI records the resolution and opens
   Decision Memory for the approved record.

5. **Review the replacement.** Return to Review and inspect **Wrenwell**.
   Confirm the replacement wording, source passage, and October 8 effective
   time. If needed, resolve it to 2026-10-08 from the explicit source sentence,
   then approve it through the UI. Leave **Kestrel Ledger** as a proposal;
   it must remain supporting-only.

6. **Open Decision Memory.** The current value should be Wrenwell. Its timeline
   should show Birchline as SUPERSEDED and Wrenwell as ACTIVE, with each
   validity boundary and source passage.

7. **Ask for the current supplier.** Submit:
   **What supplier are we currently using?**
   Expect Wrenwell, its October 8 effective time, the replaced value
   Birchline, and the source evidence.

8. **Ask for the historical value.** Use the optional date field with
   2026-10-07 and submit the same question. Expect Birchline with its historical
   validity interval and evidence.

9. **Change the source.** Return to Sources, inspect the meeting, and append a
   short note to the Wrenwell source line. Save the new source version using
   the product UI. Wait for ingestion to finish; the source version should
   change.

10. **Check Review Attention.** Open Review. The previously materialized
    Wrenwell decision should appear under **Decision records needing attention**
    with state **STALE**. Inspect it to confirm its original source passage and
    historical timeline remain visible. V1 shows the attention state; a
    dedicated stale revalidation action is deferred.

The verified fixture run kept Kestrel Ledger out of authoritative Decision
Memory. Live provider output can vary, so inspect the type and exact evidence
before approving any candidate.
