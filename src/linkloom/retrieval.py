"""Deterministic lexical retrieval and structural candidate generation."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from linkloom.schemas import CandidatePair, EvidenceRef, NoteDocument, ScoreBreakdown


TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric and CJK tokens."""
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def _quote_sha256(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def retrieve_evidence(
    query: str,
    documents: list[NoteDocument],
    max_results: int = 5,
) -> list[EvidenceRef]:
    """Perform deterministic lexical retrieval across note titles, headings, body lines, tags, and wikilinks."""
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return []

    raw_candidates: list[tuple[float, EvidenceRef]] = []
    seen_keys: set[tuple[str, int, str]] = set()

    for doc in sorted(documents, key=lambda d: d.relative_path):
        lines = doc.content.splitlines()
        heading_lines = {h["line"]: h["text"] for h in doc.headings if "line" in h}

        # 1. Match in Title (only when an exact source line can be proven)
        title_tokens = set(tokenize(doc.title))
        matched_title = query_tokens & title_tokens
        if not matched_title:
            matched_title = {q for q in query_tokens if q in doc.title.lower()}

        if matched_title:
            title_line_idx: int | None = None
            title_source_kind = "note_body"

            # (a) Match heading line corresponding to title
            for h in doc.headings:
                h_text = h.get("text", "").strip()
                if h_text.lower() == doc.title.strip().lower() or (
                    h.get("level") == 1 and any(q in h_text.lower() for q in matched_title)
                ):
                    h_line = h.get("line", 1)
                    if 1 <= h_line <= len(lines):
                        cand_quote = lines[h_line - 1]
                        cand_tokens = set(tokenize(cand_quote))
                        if matched_title & cand_tokens or any(q in cand_quote.lower() for q in matched_title) or doc.title.lower() in cand_quote.lower():
                            title_line_idx = h_line
                            title_source_kind = "heading"
                            break

            # (b) Match frontmatter title line
            if title_line_idx is None:
                for line_idx, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    if re.match(r"^title\s*:", stripped, re.IGNORECASE):
                        if any(q in line.lower() for q in matched_title) or doc.title.lower() in line.lower():
                            title_line_idx = line_idx
                            title_source_kind = "note_body"
                            break

            # (c) Match any exact content line containing the title
            if title_line_idx is None:
                for line_idx, line in enumerate(lines, start=1):
                    if doc.title.lower() in line.lower() or any(q in line.lower() for q in matched_title):
                        title_line_idx = line_idx
                        title_source_kind = "heading" if line.strip().startswith("#") else "note_body"
                        break

            if title_line_idx is not None and 1 <= title_line_idx <= len(lines):
                quote = lines[title_line_idx - 1]
                quote_tokens = set(tokenize(quote))
                if matched_title & quote_tokens or any(q in quote.lower() for q in matched_title) or doc.title.lower() in quote.lower():
                    key = (doc.relative_path, title_line_idx, quote)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        score = 4.0 + len(matched_title)
                        reason = f"Title 命中查询词: {', '.join(sorted(matched_title))}"
                        ev = EvidenceRef(
                            evidence_id="",
                            relative_path=doc.relative_path,
                            content_sha256=doc.content_sha256,
                            line_start=title_line_idx,
                            line_end=title_line_idx,
                            quote=quote,
                            quote_sha256=_quote_sha256(quote),
                            source_kind=title_source_kind,
                            reason=reason,
                            status="verified",
                        )
                        raw_candidates.append((score, ev))

        # 2. Match in headings
        for h in doc.headings:
            h_text = h.get("text", "")
            h_tokens = set(tokenize(h_text))
            matched = query_tokens & h_tokens
            if not matched:
                matched = {q for q in query_tokens if q in h_text.lower()}
            if matched:
                line_idx = h.get("line", 1)
                if 1 <= line_idx <= len(lines):
                    quote = lines[line_idx - 1]
                    quote_tokens = set(tokenize(quote))
                    # Ensure the actual source line actually contains the matched query/heading tokens
                    if matched & quote_tokens or any(q in quote.lower() for q in matched) or h_text.lower() in quote.lower():
                        key = (doc.relative_path, line_idx, quote)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            score = 3.0 + len(matched)
                            reason = f"Heading 命中查询词: {', '.join(sorted(matched))}"
                            ev = EvidenceRef(
                                evidence_id="",
                                relative_path=doc.relative_path,
                                content_sha256=doc.content_sha256,
                                line_start=line_idx,
                                line_end=line_idx,
                                quote=quote,
                                quote_sha256=_quote_sha256(quote),
                                source_kind="heading",
                                reason=reason,
                                status="verified",
                            )
                            raw_candidates.append((score, ev))

        # 3. Match in body lines
        for line_idx, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            line_tokens = set(tokenize(line))
            matched = query_tokens & line_tokens
            if not matched:
                matched = {q for q in query_tokens if q in line.lower()}

            if matched:
                key = (doc.relative_path, line_idx, line)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                # Determine source kind strictly within contract: note_body | heading | tag | wikilink
                source_kind = "note_body"
                score = 1.0 + len(matched)
                if line_idx in heading_lines or line.strip().startswith("#"):
                    source_kind = "heading"
                    score += 2.0
                elif any(f"#{t}" in line for t in doc.tags) or re.match(r"^tags\s*:", line.strip(), re.IGNORECASE):
                    source_kind = "tag"
                    score += 1.0
                elif "[[" in line and "]]" in line:
                    source_kind = "wikilink"
                    score += 1.0

                reason = f"命中查询词: {', '.join(sorted(matched))}"
                ev = EvidenceRef(
                    evidence_id="",
                    relative_path=doc.relative_path,
                    content_sha256=doc.content_sha256,
                    line_start=line_idx,
                    line_end=line_idx,
                    quote=line,
                    quote_sha256=_quote_sha256(line),
                    source_kind=source_kind,
                    reason=reason,
                    status="verified",
                )
                raw_candidates.append((score, ev))

    # Sort candidates deterministically: -score, relative_path, line_start, quote
    raw_candidates.sort(
        key=lambda item: (
            -item[0],
            item[1].relative_path,
            item[1].line_start,
            item[1].quote,
        )
    )

    results: list[EvidenceRef] = []
    for idx, (_, item) in enumerate(raw_candidates[:max_results], start=1):
        assigned_ev = EvidenceRef(
            evidence_id=f"ev_p1_{idx:04d}",
            relative_path=item.relative_path,
            content_sha256=item.content_sha256,
            line_start=item.line_start,
            line_end=item.line_end,
            quote=item.quote,
            quote_sha256=item.quote_sha256,
            source_kind=item.source_kind,
            reason=item.reason,
            status=item.status,
        )
        results.append(assigned_ev)

    return results


def _note_identifiers(doc: NoteDocument) -> set[str]:
    """Get all matching identifiers for a note (title, stem, relative path)."""
    stem = Path(doc.relative_path).stem.casefold()
    rel = doc.relative_path.casefold()
    rel_no_ext = doc.relative_path.removesuffix(".md").casefold()
    title = doc.title.casefold()
    return {stem, rel, rel_no_ext, title}


def _find_wikilink_evidence(
    source_doc: NoteDocument,
    link: str,
    target_ids: set[str],
) -> list[EvidenceRef]:
    """Find verified evidence lines in source_doc containing a wikilink to target."""
    lines = source_doc.content.splitlines()
    evidence: list[EvidenceRef] = []
    link_cf = link.casefold()

    for line_idx, line in enumerate(lines, start=1):
        if "[[" not in line:
            continue
        line_cf = line.casefold()
        targets = [
            m.split("|", maxsplit=1)[0].split("#", maxsplit=1)[0].strip().casefold()
            for m in re.findall(r"\[\[([^\]]+)\]\]", line)
        ]
        if (
            link_cf in line_cf
            or any(t == link_cf or t in target_ids or link_cf in t for t in targets)
            or any(ident in line_cf for ident in target_ids)
        ):
            ev = EvidenceRef(
                evidence_id="",
                relative_path=source_doc.relative_path,
                content_sha256=source_doc.content_sha256,
                line_start=line_idx,
                line_end=line_idx,
                quote=line,
                quote_sha256=_quote_sha256(line),
                source_kind="wikilink",
                reason=f"WikiLink 引用: {source_doc.relative_path} -> {link}",
                status="verified",
            )
            evidence.append(ev)
    return evidence


def _find_shared_tag_evidence(
    doc: NoteDocument,
    tag: str,
) -> list[EvidenceRef]:
    """Find verified evidence lines in doc containing a shared tag (frontmatter or inline)."""
    lines = doc.content.splitlines()
    evidence: list[EvidenceRef] = []
    tag_cf = tag.casefold()
    inline_tag_cf = f"#{tag_cf}"

    for line_idx, line in enumerate(lines, start=1):
        line_cf = line.casefold()
        is_tag_line = (
            inline_tag_cf in line_cf
            or (
                tag_cf in line_cf
                and (
                    line.strip().startswith("-")
                    or line.strip().startswith("tags:")
                    or "#" in line
                    or any(t.casefold() == tag_cf for t in doc.tags)
                )
            )
        )
        if is_tag_line:
            ev = EvidenceRef(
                evidence_id="",
                relative_path=doc.relative_path,
                content_sha256=doc.content_sha256,
                line_start=line_idx,
                line_end=line_idx,
                quote=line,
                quote_sha256=_quote_sha256(line),
                source_kind="tag",
                reason=f"共享标签: {tag}",
                status="verified",
            )
            evidence.append(ev)
    return evidence


def _find_shared_heading_token_evidence(
    doc: NoteDocument,
    token: str,
) -> list[EvidenceRef]:
    """Find verified heading evidence lines in doc containing a shared heading token."""
    lines = doc.content.splitlines()
    evidence: list[EvidenceRef] = []
    token_cf = token.casefold()

    heading_line_numbers: set[int] = set()
    for h in doc.headings:
        h_text = h.get("text", "")
        h_tokens = [t.casefold() for t in tokenize(h_text)]
        if token_cf in h_tokens or token_cf in h_text.casefold():
            line_idx = h.get("line")
            if line_idx is not None and 1 <= line_idx <= len(lines):
                quote = lines[line_idx - 1]
                if token_cf in quote.casefold() or token_cf in [t.casefold() for t in tokenize(quote)]:
                    heading_line_numbers.add(line_idx)
                    ev = EvidenceRef(
                        evidence_id="",
                        relative_path=doc.relative_path,
                        content_sha256=doc.content_sha256,
                        line_start=line_idx,
                        line_end=line_idx,
                        quote=quote,
                        quote_sha256=_quote_sha256(quote),
                        source_kind="heading",
                        reason=f"共享标题词: {token}",
                        status="verified",
                    )
                    evidence.append(ev)

    for line_idx, line in enumerate(lines, start=1):
        if line_idx not in heading_line_numbers and line.strip().startswith("#"):
            if token_cf in line.casefold() or token_cf in [t.casefold() for t in tokenize(line)]:
                ev = EvidenceRef(
                    evidence_id="",
                    relative_path=doc.relative_path,
                    content_sha256=doc.content_sha256,
                    line_start=line_idx,
                    line_end=line_idx,
                    quote=line,
                    quote_sha256=_quote_sha256(line),
                    source_kind="heading",
                    reason=f"共享标题词: {token}",
                    status="verified",
                )
                evidence.append(ev)

    return evidence


def find_relation_candidates_with_evidence(
    documents: list[NoteDocument],
    query: str | None = None,
    max_candidates: int = 10,
) -> tuple[list[CandidatePair], list[EvidenceRef]]:
    """Find structural and lexical relation candidates between note pairs and return verified evidence registry."""
    sorted_docs = sorted(documents, key=lambda d: d.relative_path)
    query_tokens = set(tokenize(query)) if query else set()

    candidate_list: list[tuple[float, CandidatePair, list[EvidenceRef]]] = []

    for i in range(len(sorted_docs)):
        for j in range(i + 1, len(sorted_docs)):
            doc_a = sorted_docs[i]
            doc_b = sorted_docs[j]

            # Query filter: if query provided, at least one document should contain query token
            if query_tokens:
                doc_a_tokens = set(tokenize(doc_a.content)) | set(tokenize(doc_a.title))
                doc_b_tokens = set(tokenize(doc_b.content)) | set(tokenize(doc_b.title))
                if not ((query_tokens & doc_a_tokens) or (query_tokens & doc_b_tokens)):
                    continue

            score_breakdown: list[dict[str, Any]] = []

            # 1. Check WikiLinks
            b_ids = _note_identifiers(doc_b)
            a_ids = _note_identifiers(doc_a)

            links_a_to_b = [link for link in doc_a.wikilinks if link.casefold() in b_ids]
            links_b_to_a = [link for link in doc_b.wikilinks if link.casefold() in a_ids]

            for link in links_a_to_b:
                score_breakdown.append(
                    ScoreBreakdown(kind="wikilink", value=f"{doc_a.relative_path} -> {link}", weight=2.0).to_dict()
                )
            for link in links_b_to_a:
                score_breakdown.append(
                    ScoreBreakdown(kind="wikilink", value=f"{doc_b.relative_path} -> {link}", weight=2.0).to_dict()
                )

            # 2. Check Shared Tags
            shared_tags = sorted(set(doc_a.tags) & set(doc_b.tags))
            for tag in shared_tags:
                score_breakdown.append(
                    ScoreBreakdown(kind="shared_tag", value=tag, weight=1.0).to_dict()
                )

            # 3. Check Shared Heading Tokens
            a_heading_tokens = {t for h in doc_a.headings for t in tokenize(h["text"])}
            b_heading_tokens = {t for h in doc_b.headings for t in tokenize(h["text"])}
            shared_heading_tokens = sorted((a_heading_tokens & b_heading_tokens) - {"笔记", "测试", "md", "overview"})
            for tok in shared_heading_tokens:
                score_breakdown.append(
                    ScoreBreakdown(kind="shared_heading_token", value=tok, weight=0.5).to_dict()
                )

            if not score_breakdown:
                continue

            rank_score = sum(item["weight"] for item in score_breakdown)

            # Collect verified structural-signal evidence references for this pair
            pair_structural_evidence: list[EvidenceRef] = []
            pair_seen: set[tuple[str, int, str]] = set()

            for item in score_breakdown:
                kind = item.get("kind")
                val = item.get("value", "")
                if kind == "wikilink":
                    if val.startswith(f"{doc_a.relative_path} -> "):
                        link = val.split("->", maxsplit=1)[-1].strip()
                        evs = _find_wikilink_evidence(doc_a, link, b_ids)
                    elif val.startswith(f"{doc_b.relative_path} -> "):
                        link = val.split("->", maxsplit=1)[-1].strip()
                        evs = _find_wikilink_evidence(doc_b, link, a_ids)
                    else:
                        link = val
                        evs = _find_wikilink_evidence(doc_a, link, b_ids) + _find_wikilink_evidence(doc_b, link, a_ids)

                    for ev in evs:
                        key = (ev.relative_path, ev.line_start, ev.quote)
                        if key not in pair_seen:
                            pair_seen.add(key)
                            pair_structural_evidence.append(ev)

                elif kind == "shared_tag":
                    tag = val
                    for doc in (doc_a, doc_b):
                        for ev in _find_shared_tag_evidence(doc, tag):
                            key = (ev.relative_path, ev.line_start, ev.quote)
                            if key not in pair_seen:
                                pair_seen.add(key)
                                pair_structural_evidence.append(ev)

                elif kind == "shared_heading_token":
                    tok = val
                    for doc in (doc_a, doc_b):
                        for ev in _find_shared_heading_token_evidence(doc, tok):
                            key = (ev.relative_path, ev.line_start, ev.quote)
                            if key not in pair_seen:
                                pair_seen.add(key)
                                pair_structural_evidence.append(ev)

            # Every emitted candidate must have at least one deterministic, verified EvidenceRef
            # whose exact quote supports at least one of that candidate's score_breakdown signals;
            # if no supporting exact quote exists, omit the candidate.
            if not pair_structural_evidence:
                continue

            pair_structural_evidence.sort(key=lambda ev: (ev.relative_path, ev.line_start, ev.quote))

            pair_evidence = list(pair_structural_evidence)

            # If room permits and query is provided, collect supplemental query evidence
            if query and len(pair_evidence) < 3:
                query_evs = retrieve_evidence(query, [doc_a, doc_b], max_results=3)
                for ev in query_evs:
                    key = (ev.relative_path, ev.line_start, ev.quote)
                    if key not in pair_seen:
                        pair_seen.add(key)
                        pair_evidence.append(ev)
                    if len(pair_evidence) >= 3:
                        break

            unassigned_candidate = CandidatePair(
                candidate_id="",
                left={"relative_path": doc_a.relative_path, "content_sha256": doc_a.content_sha256},
                right={"relative_path": doc_b.relative_path, "content_sha256": doc_b.content_sha256},
                rank_score=round(rank_score, 2),
                score_breakdown=score_breakdown,
                evidence=[],
                reason="存在显式 WikiLink 或共享结构信号",
                provenance="system_observation",
                status="candidate",
            )
            candidate_list.append((rank_score, unassigned_candidate, pair_evidence[:3]))

    # Sort deterministically
    candidate_list.sort(
        key=lambda item: (
            -item[0],
            item[1].left["relative_path"],
            item[1].right["relative_path"],
        )
    )

    top_entries = candidate_list[:max_candidates]

    # Build global evidence registry across top candidate pairs
    global_evidence_map: dict[tuple[str, int, str], EvidenceRef] = {}
    for _, _, pair_evs in top_entries:
        for ev in pair_evs:
            key = (ev.relative_path, ev.line_start, ev.quote)
            if key not in global_evidence_map:
                global_evidence_map[key] = ev

    sorted_keys = sorted(global_evidence_map.keys(), key=lambda k: (k[0], k[1], k[2]))
    key_to_id: dict[tuple[str, int, str], str] = {}
    final_evidence_list: list[EvidenceRef] = []
    for idx, key in enumerate(sorted_keys, start=1):
        ev_id = f"ev_p1_{idx:04d}"
        key_to_id[key] = ev_id
        orig_ev = global_evidence_map[key]
        final_evidence_list.append(
            EvidenceRef(
                evidence_id=ev_id,
                relative_path=orig_ev.relative_path,
                content_sha256=orig_ev.content_sha256,
                line_start=orig_ev.line_start,
                line_end=orig_ev.line_end,
                quote=orig_ev.quote,
                quote_sha256=orig_ev.quote_sha256,
                source_kind=orig_ev.source_kind,
                reason=orig_ev.reason,
                status=orig_ev.status,
            )
        )

    final_candidates: list[CandidatePair] = []
    for idx, (_, cand, pair_evs) in enumerate(top_entries, start=1):
        cand_ev_ids = [key_to_id[(ev.relative_path, ev.line_start, ev.quote)] for ev in pair_evs]
        assigned_cand = CandidatePair(
            candidate_id=f"pair_p1_{idx:04d}",
            left=cand.left,
            right=cand.right,
            rank_score=cand.rank_score,
            score_breakdown=cand.score_breakdown,
            evidence=cand_ev_ids,
            reason=cand.reason,
            provenance=cand.provenance,
            status=cand.status,
        )
        final_candidates.append(assigned_cand)

    return final_candidates, final_evidence_list


def find_relation_candidates(
    documents: list[NoteDocument],
    query: str | None = None,
    max_candidates: int = 10,
) -> list[CandidatePair]:
    """Find structural and lexical relation candidates between note pairs."""
    candidates, _ = find_relation_candidates_with_evidence(
        documents=documents, query=query, max_candidates=max_candidates
    )
    return candidates
