"""Author-visible synthetic split construction; never imported by a generator."""
import hashlib
import json
from pathlib import Path


def build(root):
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": "self-improvement-splits/v1", "author_visible_gold": True,
                "note": "Distinct entities and files; synthetic schema transfer, not unseen natural-data generalization",
                "files": {}}
    for split, project, owner, leader, other in (
        ("dev", "Glacier Bay", "Mira Cole", "Owen Park", "Tara West"),
        ("validation", "Amber Ridge", "Nina Reed", "Evan Shaw", "Luca Page"),
        ("final", "Silver Meadow", "Lena Hart", "Theo Stone", "Iris Lake"),
    ):
        workspace = split + "-lab"
        cases, gold = [], []
        for kind in ("normal", "ranking", "missing", "conflict", "foreign", "alias"):
            docs = [{"workspace_id": workspace, "path": "owner.md",
                     "content": f"{owner} is the accountable owner of {project}."}]
            if kind not in {"missing", "foreign"}:
                docs.append({"workspace_id": workspace, "path": "leader.md",
                             "content": f"{owner} reports directly to {leader}."})
            if kind == "ranking":
                for i in range(5):
                    docs.append({"workspace_id": workspace, "path": f"guide{i}.md",
                                 "content": f"Who is the direct leader of {owner}? This direct leader handbook is a question, with no reporting fact. Section {i}."})
            if kind == "conflict":
                docs.append({"workspace_id": workspace, "path": "conflict.md",
                             "content": f"{owner} reports directly to {other}."})
            if kind == "foreign":
                docs.append({"workspace_id": "foreign-workspace", "path": "private.md",
                             "content": f"{owner} reports directly to {leader}."})
            query = f"Who is the direct leader of the owner of {project}?"
            values, sources = [owner.lower(), leader.lower()], ["owner.md", "leader.md"]
            if kind == "alias":
                alias = project.split()[0] + "-Key"
                query = f"Who owns the project behind {alias} and who leads that owner?"
                docs.append({"workspace_id": workspace, "path": "alias.md",
                             "content": f"{alias} is the team alias for {project}."})
                values.insert(0, project.lower())
                sources.insert(0, "alias.md")
            refused = kind in {"missing", "conflict", "foreign"}
            if refused:
                values, sources = [owner.lower()], ["owner.md"]
                if kind == "conflict":
                    sources += ["leader.md", "conflict.md"]
            case_id = split + "-" + kind
            cases.append({"id": case_id, "query": query, "workspace_id": workspace,
                          "as_of": None, "documents": docs})
            gold.append({"id": case_id, "expected": {"outcome": "partial" if refused else "complete",
                         "values": values, "reason": "conflicting_evidence" if kind == "conflict" else
                         "evidence_insufficient" if refused else None, "required_sources": sources}})
        for label, rows in (("public", cases), ("gold", gold)):
            path = root / f"{split}_{label}.json"
            if path.exists():
                raise FileExistsError("Refuse to rewrite frozen fixtures")
            raw = (json.dumps({"partition": split, "cases": rows}, indent=2) + "\n").encode()
            path.write_bytes(raw)
            manifest["files"][path.name] = hashlib.sha256(raw).hexdigest()
    (root / "freeze.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[2] / "tests/fixtures/self_improvement")
