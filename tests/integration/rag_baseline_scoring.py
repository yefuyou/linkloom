"""Offline scoring and a blinded rubric for content and stale answers."""

from collections import defaultdict
from pathlib import Path
from statistics import mean
from decimal import Decimal

from run_rag_baseline import (OUTPUT, cases, completed, dump, hash_text, normalize,
                              read, require, verify_selection)


RUBRIC = {
    "version": 1,
    "strict": "Apply the original scorer and every assertion for the split.",
    "content": [
        "Apply the same expected outcome, factual values, role, identity, temporal scope, "
        "list membership, ambiguity and no-positive-facts checks.",
        "Keep original-source citation requirements, factual relation consistency and relevant "
        "comparison details such as both values, dates, and which item changed.",
        "Ignore requirements for a formal receipt, graph IDs, verified record, transition edge, "
        "supersedes chain, graph-bound structured output, or other graph-native proof. "
        "For mixed assertions keep the factual proposition and omit only its formal proof.",
        "Assess the answer and selected facts, never facts present only in cited background. "
        "A historical fact in its correct historical role is not a current assertion. "
        "A technically invalid or missing answer fails. Do not resolve ambiguity using Gold.",
        "Apply to both routes with the same rubric, blind to route and strict result. "
        "Record a short reason for every answer, including which formal requirements were omitted.",
    ],
    "stale": [
        "One count per answer if it asserts an old decision, owner, or deadline as effective "
        "at the time asked and the frozen reference has already replaced it at that time.",
        "Use question as_of or the campaign query_time for current questions. For meeting comparisons "
        "use each named endpoint. Accept old values cited as history or valid at an earlier endpoint.",
        "Use confirmed decision replacement dates and consecutive task states from the frozen fixture "
        "only in scoring. Do not count an unrelated error, omission, or refusal as stale. "
        "Mark an asserted value with ambiguous identity/time as unresolved and give a reason. "
        "A stale finding includes the old value, replacement, and effective date.",
    ],
}


class Scoring:
    def __init__(self, archive, stage):
        from graph_eval_date_score import DateParityScorer
        from graph_acceptance_v2 import AcceptanceEvaluator
        from graph_acceptance_extensions import AcceptanceEvaluator as D3Evaluator

        base = archive / "graph-contract-repair"
        self.stage = stage
        self.public = {row[0]: row[2:] for row in cases(archive, stage)}
        if stage == "D3":
            # Nothing that parses Gold or saved graph answers runs before this gate.
            completed("D3")
            data = read(archive / "graph-eval-holdout-D3.json")
            self.fixture = {"D3": next(iter(data["fixtures"].values()))}
            self.index = {"D3": read(base / "D3/adapter-confirmation-index.json")}
            self.expected = {q["id"]: q["expected"] for q in data["questions"]}
            self.assertions = {r["id"]: r["assertions"]
                               for r in read(base / "D3/assertions.json")["rows"]}
            self.scorers = {"D3": D3Evaluator(self.fixture["D3"], self.index["D3"])}
        else:
            dev = read(archive / "graph-eval-questions.json")
            d2 = read(archive / "graph-eval-holdout-D2.json")
            self.fixture = {"D": dev["fixtures"]["graph-dev-meetings"],
                            "D2": next(iter(d2["fixtures"].values()))}
            d_index = read(archive / "graph-eval-confirmations-v1.json")
            self.index = {"D": d_index["meetings"],
                          "D2": read(archive / "graph-holdout-D2/confirmation-index.json")}
            self.expected = {q["id"]: q["expected"] for q in dev["questions"] + d2["questions"]}
            self.assertions = {r["id"]: r["assertions"] for r in
                               read(archive / "graph-holdout-D2/assertions.json")["rows"]}
            self.scorers = {"D": DateParityScorer(dev["fixtures"], d_index),
                            "D2": AcceptanceEvaluator(self.fixture["D2"], self.index["D2"])}

    def raw_line_proofs(self, group, identifier, citations):
        # Bind only cited public source bytes. Confirmation records and edges are
        # never included in the candidate's scored output.
        _, public_fixture, _ = self.public[identifier]
        documents = {d["path"]: d["content"] for d in public_fixture["documents"]
                     if d["workspace_id"] == public_fixture["workspace_id"]}
        source_index = self.index[group].get("sources", {})
        meetings = {m["id"]: m for m in self.fixture[group]["meetings"]}
        proofs = []
        for citation in citations:
            path, number, quote = citation["source"], citation["line"], citation["quote"]
            content = documents[path]
            source_key = Path(path).stem
            meeting = meetings.get(source_key)
            source = source_index.get(source_key)
            if not meeting or not source or content != "\n".join(meeting["lines"]):
                continue
            digest = hash_text(content)
            require(digest == source.get("content_sha256", digest), "SOURCE_BYTES_MISMATCH")
            require(1 <= number <= len(meeting["lines"]) and meeting["lines"][number-1] == quote,
                    "CITATION_BYTES_MISMATCH")
            proofs.append(dict(workspace_id=self.index[group]["workspace_id"], quote=quote,
                source_id=source["source_id"], source_version_id=source["source_version_id"],
                content_sha256=digest, quote_sha256=hash_text(quote), line_start=number, line_end=number))
        return proofs

    def score(self, identifier, group, actual):
        from run_graph_continuation import unknown_positive

        if group == "D":
            public, fixture, _ = self.public[identifier]
            docs = [d for d in fixture["documents"] if d["workspace_id"] == fixture["workspace_id"]]
            strict = self.scorers[group](identifier, actual, self.expected[identifier], public, docs)
        else:
            strict = self.scorers[group](actual, self.assertions[identifier])
        return strict, unknown_positive(actual, self.expected[identifier])

    def rag_actual(self, row):
        try:
            actual = normalize(row["raw"], row["passages"])
        except (ValueError, TypeError, KeyError):
            actual = dict(status="RUNTIME_FAILURE", answer="", values=[], relations=[],
                          citations=[], invalid_citations=0)
        group, identifier = row["group"], row["id"]
        proofs = self.raw_line_proofs(group, identifier, actual["citations"])
        return actual | dict(evidence=[r["source"] for r in actual["citations"]]
                             if not actual["invalid_citations"] else [],
                             structured_outputs=[], result_steps=[dict(id="rag", status=actual["status"],
                             evidence=proofs, records=[], edges=[])])


def strict_rows(archive, stage, rows):
    scorer = Scoring(archive, stage)
    result = []
    for row in rows:
        actual = scorer.rag_actual(row)
        strict, invented = scorer.score(row["id"], row["group"], actual)
        result.append(row | dict(actual=actual, strict=strict, invented=invented))
    return result


def graph_rows(archive, stage, scorer):
    report = read(archive / "graph-contract-repair" / stage / "report.json")
    require(report["status"] == "COMPLETE", "GRAPH_REPORT_INCOMPLETE")
    rows = []
    for row in report["rows"]:
        if row["route"] != "graph_only" or row["group"] not in scorer.scorers:
            continue
        strict, invented = scorer.score(row["id"], row["group"], row["actual"])
        require(strict == row["score"] and invented == row["unknown_positive_answer"],
                "SAVED_STRICT_SCORE_MISMATCH: " + row["id"])
        rows.append(row | dict(strict=strict, invented=invented, system="LinkLoom"))
    require(len(rows) == (36 if stage == "D3" else 84), "GRAPH_ROW_COUNT_CHANGED")
    return rows


def check_dev_parity(archive):
    return len(graph_rows(archive, "DEV", Scoring(archive, "DEV")))


def evidence(actual):
    rag = actual.get("citations")
    if rag is not None:
        return rag
    result = []
    for step in actual.get("result_steps", []):
        for proof in step.get("evidence", []):
            ref = dict(source=proof.get("source_id"), line=proof.get("line_start"),
                       quote=proof.get("quote"))
            if ref not in result:
                result.append(ref)
    return result


def review_inputs(archive):
    selected = verify_selection(archive)
    rows, sheets = {}, {}
    for stage in ("DEV", "D3"):
        paid = completed(stage)
        scorer = Scoring(archive, stage)
        rag = strict_rows(archive, stage, [r for r in paid if r["config"] == selected])
        graph = graph_rows(archive, stage, scorer)
        for row in [r | dict(system="RAG") for r in rag] + graph:
            identifier = row["id"]
            public, _, query_time = scorer.public[identifier]
            actual = row["actual"]
            token = hash_text(f"{stage}:{row['group']}:{identifier}:{row['repeat']}:{row['system']}")[:20]
            rows[token] = row
            group = row["group"]
            fixture = scorer.fixture[group]
            sheets[token] = dict(group=group, id=identifier, question=public["question"],
                as_of=public.get("as_of"), query_time=query_time, expected=scorer.expected[identifier],
                assertions=scorer.assertions.get(identifier), status=actual["status"], answer=actual.get("answer"),
                values=actual.get("values", []), relations=actual.get("relations", []),
                citations=evidence(actual), timeline=dict(
                    decisions=fixture["confirmed_decisions"], tasks=fixture["confirmed_tasks"],
                    meetings=[{k: v for k, v in m.items() if k != "lines"} for m in fixture["meetings"]]))
    return rows, dict(sorted(sheets.items()))


def report_or_review(archive, action):
    rows, sheets = review_inputs(archive)
    sheet = OUTPUT / "blind-review.json"
    if action == "review":
        dump(sheet, dict(rubric=RUBRIC, answers=sheets))
        print("Write blind-judgments.json using the review IDs, with content_correct, stale, reason.")
        return
    require(read(sheet) == dict(rubric=RUBRIC, answers=sheets), "REVIEW_INPUT_CHANGED")
    judgments = read(OUTPUT / "blind-judgments.json")
    require(set(judgments) == set(rows), "INCOMPLETE_BLIND_ADJUDICATION")
    for token, row in rows.items():
        judge = judgments[token]
        require(type(judge.get("content_correct")) is bool and
                judge.get("stale") in {"yes", "no", "unresolved"} and
                isinstance(judge.get("reason"), str) and bool(judge["reason"].strip()) and
                (judge["stale"] != "yes" or all(isinstance(judge.get(key), str) and judge[key].strip()
                 for key in ("old_value", "replacement", "effective_date"))), "INVALID_BLIND_JUDGMENT")
        row.update({key: judge[key] for key in ("content_correct", "stale", "reason")})
        if judge["stale"] == "yes":
            row.update({key: judge[key] for key in ("old_value", "replacement", "effective_date")})
    groups, pairs = defaultdict(list), defaultdict(dict)
    for row in rows.values():
        groups[row["group"], row["system"]].append(row)
        pairs[row["group"], row["id"], row["repeat"]][row["system"]] = row
    lines = ["# RAG vs LinkLoom 时态实验", "", f"DEV 胜出配置 {verify_selection()}。",
             "", "正确率为 3 次均值 [最小, 最大]。费用为已知实际用量。",
             "", "|组|系统|严格|内容|错误关系|编造|拒答|延迟 ms|USD|用量未知|过期|待判|",
             "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def interval(items, check):
        scores = [mean(check(r) for r in items if r["repeat"] == n) for n in range(1, 4)]
        return f"{mean(scores):.1%} [{min(scores):.1%}, {max(scores):.1%}]"
    for (group, system), items in sorted(groups.items()):
        strict = interval(items, lambda row: row["strict"]["correct"])
        content = interval(items, lambda row: row["content_correct"])
        errors = sum(r["strict"]["error_relations"] for r in items)
        invented = sum(r["invented"] for r in items)
        refused = mean(r["strict"]["refused"] for r in items)
        latency = mean(r["elapsed_ms"] for r in items)
        usd = sum(Decimal(str(r.get("known_actual_cost_usd", r.get("cost_usd")) or 0)) for r in items)
        unknown_usage = sum(r.get("unknown_usage_calls", 0) for r in items)
        stale = sum(r["stale"] == "yes" for r in items)
        unresolved = sum(r["stale"] == "unresolved" for r in items)
        lines.append(f"|{group}|{system}|{strict}|{content}|{errors}|{invented}|{refused:.1%}|"
                     f"{latency:.0f}|{usd:.6f}|{unknown_usage}|{stale}|{unresolved}|")

    def cell(row):
        answer = str(row["actual"].get("answer") or "—")
        cites = " / ".join(f'{r["source"]}#{r["line"]} {r["quote"]}' for r in evidence(row["actual"]))
        obsolete = row["stale"]
        if obsolete == "yes":
            obsolete += f' ({row["old_value"]} → {row["replacement"]}, {row["effective_date"]})'
        failed = ", ".join(key for key, ok in row["strict"]["checks"].items() if not ok)
        text = (f"{answer} [{cites}] / 严格 {int(row['strict']['correct'])} / "
                f"内容 {int(row['content_correct'])} / 过期 {obsolete} / {failed} / {row['reason']}")
        return text.replace("|", "\\|").replace("\n", " ")

    for key, check in (("严格分", lambda r: r["strict"]["correct"]),
                       ("内容分", lambda r: r["content_correct"])):
        for winner, loser in (("RAG", "LinkLoom"), ("LinkLoom", "RAG")):
            matched = [f"{g}/{i}/{n}" for (g, i, n), p in sorted(pairs.items())
                       if check(p[winner]) and not check(p[loser])]
            lines += ["", f"{key} {winner} 对而 {loser} 错：" + ("、".join(matched) or "无")]
    lines += ["", "## 逐题对比", "", "|组|题号|重复|RAG|LinkLoom|",
              "|---|---|---:|---|---|"]
    for (group, identifier, repeat), pair in sorted(pairs.items()):
        lines.append(f"|{group}|{identifier}|{repeat}|{cell(pair['RAG'])}|{cell(pair['LinkLoom'])}|")
    path = OUTPUT.parent / "rag-vs-linkloom.md"
    with path.open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
