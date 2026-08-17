"""Read-only Ask service that returns grounded evidence and extractive summaries."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from linkloom.loader import LoaderError, VaultReader, _is_inside
from linkloom.retrieval import retrieve_evidence
from linkloom.schemas import AskAnswer, AskResult


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


def _build_ask_report(result: AskResult) -> str:
    lines = [
        "# Ask Report",
        "",
        f"- Query: `{result.query}`",
        f"- Status: `{result.status}`",
        f"- Evidence count: {len(result.evidence)}",
        f"- Index SHA-256: `{result.source.get('index_sha256', '')}`",
        "",
        "## Answer",
        "",
        result.answer.get("text", ""),
        "",
        "## Evidence",
        "",
    ]
    if result.evidence:
        lines.append("| ID | Note | Line | Quote | Quote SHA-256 | Reason |")
        lines.append("|---|---|---:|---|---|---|")
        for ev in result.evidence:
            quote_clean = ev["quote"].replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{ev['evidence_id']}` | `{ev['relative_path']}` | {ev['line_start']} | {quote_clean} | `{ev['quote_sha256'][:16]}...` | {ev['reason']} |"
            )
    else:
        lines.append("- No evidence found.")

    lines.append("")
    return "\n".join(lines)


class AskService:
    """Service to execute cited, grounded note querying."""

    @classmethod
    def run(
        cls,
        vault_root: Path | str,
        index_path: Path | str,
        query: str,
        output_dir: Path | str | None = None,
        max_results: int = 5,
        request_id: str = "req_p1_0001",
        run_id: str = "run_p1_0001",
    ) -> AskResult:
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

        evidence_refs = retrieve_evidence(query=query, documents=documents, max_results=max_results)
        evidence_dicts = [ev.to_dict() for ev in evidence_refs]

        if not evidence_dicts:
            status = "no_evidence"
            answer = AskAnswer(
                text="未在笔记中找到与查询相关的证据。请尝试调整查询词或检查已索引内容。",
                kind="extractive_summary",
                provenance="system_observation",
            ).to_dict()
        else:
            status = "completed"
            answer = AskAnswer(
                text=f"找到 {len(evidence_dicts)} 条相关证据；请根据引用查看原文。",
                kind="extractive_summary",
                provenance="system_observation",
            ).to_dict()

        result = AskResult(
            schema_version=1,
            result_type="ask",
            request_id=request_id,
            run_id=run_id,
            status=status,
            query=query,
            answer=answer,
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
            _write_text(report_file, _build_ask_report(result))

        return result
