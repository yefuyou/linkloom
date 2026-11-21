# Trajectory Diff demo

`demo.html` is a single-file, offline page (no CDN, no external resources). Open it directly by double-clicking it, or from the repo root:

- Windows PowerShell: `Invoke-Item .\examples\trajectory-diff\demo.html`
- Generic: open `examples/trajectory-diff/demo.html` in any browser.

## Regenerate

From the repo root:

```powershell
python scripts/export_trajectory_diff.py demo --out out/demo
```

```sh
python scripts/export_trajectory_diff.py demo --out out/demo
```

This writes `out/demo/trajectory_diff.html`. The `out/` directory and generated `trajectory_diff.html` files are git-ignored.

## Notes

- Regenerated pages use newly created run_ids, so they differ from the committed `demo.html` in those identifiers.
- The demo uses a synthetic 2-note vault and mock traces. The mock model is not real inference and this is not multi-agent validation.
