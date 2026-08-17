"""Read-only Connect service that discovers and explains note relation candidates."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from linkloom.loader import LoaderError, VaultReader, _is_inside
from linkloom.retrieval import find_relation_candidates_with_evidence
from linkloom.schemas import ConnectResult


def _write_text(path: Path, content: str) -> None:
    temporary_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    )
    try:
        with temporary_file:
            temporary_file.write(content)
        os.replace(temporary_file.name, path)
    except Exception:
        Path(temporary_file.name).unlink(missing_ok=True)
        raise


def _build_connect_report(result: ConnectResult) -> str:
    lines = [
        "# Connect Report",
        "",
        f"- Query: `{result.query}`",
        f"- Status: `{result.status}`",
        f"- Candidates found: {len(result.candidates)}",
        f"- Evidence count: {len(result.evidence)}",
        f"- Index SHA-256: `{result.source.get('index_sha256', '')}`",
        "",
        "## Candidates",
        "",
    ]
    if result.candidates:
        lines.append("| ID | Left Note | Right Note | Rank Score | Evidence | Reason | Signals |")
        lines.append("|---|---|---|---:|---|---|---|")
        for cand in result.candidates:
            signals = ", ".join(
                f"{item['kind']}:{item['value']} ({item['weight']})"
                for item in cand.get("score_breakdown", [])
            )
            ev_str = ", ".join(f"`{e}`" for e in cand.get("evidence", []))
            lines.append(
                f"| `{cand['candidate_id']}` | `{cand['left']['relative_path']}` | `{cand['right']['relative_path']}` | {cand['rank_score']} | {ev_str} | {cand['reason']} | {signals} |"
            )
    else:
        lines.append("- No relation candidates found.")

    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    if result.evidence:
        lines.append("| ID | Note | Line | Quote | Quote SHA-256 | Source Kind | Reason |")
        lines.append("|---|---|---:|---|---|---|---|")
        for ev in result.evidence:
            quote_clean = ev["quote"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{ev['evidence_id']}` | `{ev['relative_path']}` | {ev['line_start']} | {quote_clean} | `{ev['quote_sha256'][:16]}...` | {ev['source_kind']} | {ev['reason']} |"
            )
    else:
        lines.append("- No evidence found.")

    lines.append("")
    return "\n".join(lines)


class ConnectService:
    """Service to discover and rank relation candidates with structural breakdown."""

    @classmethod
    def run(
        cls,
        vault_root: Path | str,
        index_path: Path | str,
        query: str,
        output_dir: Path | str | None = None,
        max_candidates: int = 10,
        request_id: str = "req_p1_0002",
        run_id: str = "run_p1_0002",
    ) -> ConnectResult:
        raw_vault_root = Path(vault_root)
        resolved_root = raw_vault_root.resolve()

        if output_dir is not None:
            resolved_output = Path(output_dir).resolve()
            if _is_inside(resolved_output, resolved_root):
                raise LoaderError(
                    "Output directory must be outside the input root.",
                    code="OUTPUT_INSIDE_INPUT",
                    exit_code=2,
                )

        reader = VaultReader(vault_root=vault_root, index_path=index_path)
        documents = reader.read_notes()
        source_context = reader.get_source_context()

        candidate_refs, evidence_refs = find_relation_candidates_with_evidence(
            documents=documents, query=query, max_candidates=max_candidates
        )
        candidate_dicts = [cand.to_dict() for cand in candidate_refs]
        evidence_dicts = [ev.to_dict() for ev in evidence_refs]

        status = "completed" if candidate_dicts else "no_candidates"

        result = ConnectResult(
            schema_version=1,
            result_type="connect",
            request_id=request_id,
            run_id=run_id,
            status=status,
            query=query,
            candidates=candidate_dicts,
            evidence=evidence_dicts,
            warnings=reader.warnings,
            source=source_context.to_dict(),
        )

        if output_dir is not None:
            out_path = Path(output_dir).resolve()
            out_path.mkdir(parents=True, exist_ok=True)
            result_file = out_path / "result.json"
            report_file = out_path / "report.md"

            _write_text(
                result_file,
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            _write_text(report_file, _build_connect_report(result))

        return result
