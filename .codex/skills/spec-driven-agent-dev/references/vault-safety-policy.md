# Vault Safety Policy

linkloom may assist with organization and maintenance, but real vault content is user-owned. Treat mutation as high-trust work.

## Default Mode

- Prefer read-only inspection.
- Prefer proposals, reports, and dry-run plans.
- Keep generated proposals separate from the live vault until approved.

## Mutation Gate

Do not perform real vault mutation unless all conditions are true:

1. Reviewed dry-run output exists.
2. The human approves the exact plan ID and vault path.
3. The human approves the exact operation.
4. The operation matches the approved SPEC and task.
5. Target files are validated as unchanged since preview.
6. A recoverable backup is created before the first write.
7. The audit-log destination is defined.
8. A tested rollback path exists.

If any condition is missing, stop at planning or dry-run evidence.

## Exact Operation Examples

- Move file A to folder B.
- Rename file A to name B.
- Add frontmatter field X to files matching reviewed list Y.
- Create proposed note Z in staging folder W.

## Prohibited Shortcuts

- Do not infer broad approval from a general preference.
- Do not mutate `D:\programblog` while working on linkloom.
- Do not perform cleanup, relocation, deletion, or bulk edits without exact approval.
- Do not combine review and mutation in the same silent step.
- Do not treat a successful backup command as sufficient until rollback has
  restored the synthetic fixture in a test.

## Dry-Run Evidence

Dry-run output should list each proposed path, operation, reason, and expected resulting path or content change.

## Apply Evidence

Applied operations must record the approved plan ID, target vault, source
versions, backup location, completed operations, failed operations, and rollback
result when rollback is needed.
