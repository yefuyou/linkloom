"""Deterministic synthetic scale fixtures; no files or real vaults are read."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import json
import random

SEED = 20261029
WORKSPACE = "synthetic-scale"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SIZES = (1_000, 10_000, 100_000)


def retrieval_fixture(size: int, query_count: int = 100, seed: int = SEED):
    """Nested corpora: 10 records/group, one authoritative answer per query.

    Each group contains current + superseded approved decisions, six unapproved
    near duplicates, and two same-name decisions belonging to other projects.
    Queries contain a project key (not a document ID); answer IDs/values are
    held out from the query text. Paths do not encode relevance.
    """
    if size < 10 or size % 10:
        raise ValueError("size must be a positive multiple of 10")
    if query_count < 1:
        raise ValueError("query_count must be positive")
    rng = random.Random(seed)
    documents = []
    names = ("Atlas", "Cedar", "Orion", "Harbor", "Juniper")
    for group in range(size // 10):
        name = names[group % len(names)]
        project = f"project-{group:06d}"
        current = f"Vendor-{rng.getrandbits(48):012x}"
        previous = f"Vendor-{rng.getrandbits(48):012x}"
        for variant in range(10):
            foreign = variant >= 8
            historical = variant == 1 or variant in (5, 6, 7)
            draft = 2 <= variant <= 7
            project_key = project if not foreign else f"external-{group:06d}-{variant}"
            value = previous if historical else current
            kind = ("same_name_other_project" if foreign else
                    "near_duplicate" if draft else
                    "superseded" if historical else "current")
            authority = "Unapproved draft" if draft else "Approved decision"
            state = ("superseded before 2026-02-01; valid from 2026-01-01 to 2026-02-01"
                     if historical else "current since 2026-02-01")
            text = (f"{authority}. {project_key} {name} uses supplier {value}. "
                    f"Status: {state}. Scope: {project_key} {name}. "
                    f"Supplier selection for service operations. Review copy {variant}.")
            resource_id = f"doc-{group:06d}-{variant}"
            # Stable random-looking order prevents a current-first tie shortcut.
            path_key = hashlib.sha256(f"{seed}:{resource_id}".encode()).hexdigest()[:16]
            documents.append({
                "workspace_id": WORKSPACE, "resource_id": resource_id,
                "evidence_id": resource_id,
                "logical_path": f"/projects/{project_key}/{path_key}.md",
                "source_ref": f"synthetic/{resource_id}.md", "content": text,
                "metadata": {"kind": kind, "approved": not draft,
                             "project": project_key, "value": value},
            })
    queries = []
    # Shared anchors stay identical across the three corpus sizes.
    anchors = list(range(min(100, size // 10)))
    random.Random(seed + 1).shuffle(anchors)
    if query_count > len(anchors) * 2:
        raise ValueError("query_count exceeds available distinct gold questions")
    for index in range(query_count):
        group = anchors[(index // 2) % len(anchors)]
        historical = bool(index % 2)
        name = names[group % len(names)]
        text = (f"Which approved supplier did project-{group:06d} {name} use "
                "before 2026-02-01, valid from 2026-01-01?"
                if historical else
                f"Which approved current supplier does project-{group:06d} {name} "
                "use since 2026-02-01?")
        gold = f"doc-{group:06d}-{int(historical)}"
        queries.append({"id": f"q-{index:03d}", "query": text,
                        "kind": "historical" if historical else "current",
                        "relevant_ids": [gold],
                        "expected_value": documents[group * 10 + int(historical)]["metadata"]["value"]})
    return documents, queries


def fixture_digest(documents, queries):
    digest = hashlib.sha256()
    for row in queries:
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())
        digest.update(b"\n")
    for row in documents:
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def fixture_manifest(documents, queries):
    return {"seed": SEED, "documents": len(documents), "queries": len(queries),
            "sha256": fixture_digest(documents, queries),
            "kinds": dict(Counter(row["metadata"]["kind"] for row in documents)),
            "query_kinds": dict(Counter(row["kind"] for row in queries))}


def temporal_rows(count: int = 100_000):
    """100 versions/slot, including exact half-open date boundaries."""
    if count <= 0 or count % 100:
        raise ValueError("temporal count must be a positive multiple of 100")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for index in range(count):
        slot, version = divmod(index, 100)
        subject = f"project-{slot:06d} Atlas"
        since = start + timedelta(days=version)
        until = since + timedelta(days=1) if version < 99 else None
        yield (f"decision-{index:06d}", WORKSPACE, f"slot-{slot:06d}",
               subject, "uses supplier", subject.casefold(), "uses supplier",
               f"Vendor-{slot:06d}-v{version:02d}",
               "SUPERSEDED" if until else "CURRENT",
               "SUPERSEDED" if until else "ACTIVE",
               since.isoformat(), until.isoformat() if until else None,
               f"decision-{index - 1:06d}" if version else None,
               "synthetic-episode", "synthetic-scale")


def temporal_cases(count: int = 100_000, query_count: int = 300):
    rng = random.Random(SEED + 2)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for index in range(query_count):
        slot = rng.randrange(count // 100)
        # Before history, first record, interior, boundary, and current.
        version = (-1, 0, 49, 50, 99, 150)[index % 6]
        timestamp = start + timedelta(days=version)
        expected = None if version < 0 else f"decision-{slot * 100 + min(version, 99):06d}"
        yield {"subject": f"project-{slot:06d} Atlas", "subject_key": f"slot-{slot:06d}",
               "as_of": timestamp, "expected_id": expected}


def routing_fixture(count: int, anchors: int = 20):
    """Reviewed slot inventory and queries with independent eligible-slot sets."""
    if count < 4:
        raise ValueError("at least four slots required")
    slots = []
    for index in range(count):
        # Adjacent projects deliberately share both the label Atlas and vendor.
        pair = index // 2
        slots.append({
            "subject": f"project-{index:06d} Atlas", "relation": "uses supplier",
            "values": [f"Legacy-{index:06d}", f"Vendor-pair-{pair:06d}"],
            "current_value": f"Vendor-pair-{pair:06d}",
            "decision_ids": [f"reviewed-{index:06d}"],
        })
    queries = []
    target_indices = sorted(random.Random(SEED + 3).sample(range(count), min(count, anchors)))
    for index in target_indices:
        subject = slots[index]["subject"]
        paired = list(range(index // 2 * 2, min(count, index // 2 * 2 + 2)))
        other = (index + 2) % count
        def add(kind, text, eligible, must_abstain=False):
            queries.append({"kind": kind, "query": text,
                            "eligible": [slots[i]["subject"] for i in eligible],
                            "must_abstain": must_abstain})
        add("exact_project", f"Which supplier does {subject} use?", [index])
        add("old_value", f"Why did we switch from Legacy-{index:06d}?", [index])
        add("same_name", "Which supplier does Atlas use?", list(range(count)), True)
        add("shared_current_value",
            f"Why did we switch to Vendor-pair-{index // 2:06d}?", paired, True)
        add("cross_project",
            f"Compare {subject} and {slots[other]['subject']} suppliers", [index, other], True)
        add("unknown_project", f"Which supplier does ghost-{index:06d} Atlas use?", [], True)
    return slots, queries
