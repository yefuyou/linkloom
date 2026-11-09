"""Offline Same Task Hint title-cosine sweep for the frozen external dev v1."""
from __future__ import annotations

import argparse
from fractions import Fraction
from collections import Counter
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
HASHES = {
    "pool.json": "df6244fa6dd95673017236a2c39c644dc937e97e8f955581d41b70a989831234",
    "cases.jsonl": "e35925f32c67b8a6c39f4fccb91b14c3b80f01be77ef3baecfa0a339fb467152",
    "README.md": "b4c4016677bacfc9df64cd6ec28d06520ccf0ee5e10f883c1fea1d867b39a1b3",
}
GROUPS = ("正例", "相似负例", "跨项目同名")
POINTS = range(55, 96)


def load_fixture(directory):
    files = {name: (directory / name).read_bytes() for name in HASHES}
    for name, raw in files.items():
        if sha256(raw).hexdigest() != HASHES[name]:
            raise ValueError(f"SHA-256 mismatch: {name}")
    return files


def group(case):
    return dict(positive=GROUPS[0], negative=GROUPS[1], cross_project=GROUPS[2])[case["kind"].split("/")[0]]


def project_labels(pool, cases):
    sources = {row["source_id"]: row["content"].splitlines() for row in pool["sources"]}
    task_labels = {}
    for task in pool["tasks"]:
        source, line = task["evidence"].rsplit("#L", 1)
        label = task["project_from_source"]
        if (task["quote"] not in sources[source][int(line) - 1]
                or label not in task["quote"] or not label.endswith("项目")):
            raise ValueError(f"Unverified source project: {task['id']}")
        task_labels[task["id"]] = label
    candidates = {}
    for case in cases:
        source = case["candidate"]["source_line"]
        sentence = re.sub(r"^\[[^\]]+\]\s*[^:：]+[:：]\s*", "", source, count=1)
        labels = {label for label in task_labels.values() if label.removesuffix("项目") in sentence}
        if len(labels) != 1:
            raise ValueError(f"Non-unique source project: {case['case_id']}")
        candidates[case["case_id"]] = labels.pop()
    return task_labels, candidates


def evaluate(embedder, tasks, cases, projects=None, *, owners=False):
    import numpy as np

    task_labels, candidate_labels = projects or ({}, {})
    def text(title, label, owner):
        return " ".join(part for part in (label, title, owner if owners else None) if part)

    task_texts = [text(task["title"], task_labels.get(task["id"]), task["owner"]) for task in tasks]
    rows = []
    for case in cases:
        identifier = case["case_id"]
        texts = [text(case["candidate"]["title"], candidate_labels.get(identifier), case["candidate"]["owner"]), *task_texts]
        # SameTaskHintService._rank_local uses float64 normalization of
        # the encoder's float32 vectors followed by a dot product per hit.
        vectors = np.asarray(embedder.encode(texts), dtype=float)
        if vectors.ndim != 2 or len(vectors) != len(texts) or not np.isfinite(vectors).all():
            raise ValueError(f"Invalid title vectors: {identifier}")
        norms = np.linalg.norm(vectors, axis=1)
        if (norms == 0).any():
            raise ValueError(f"Zero-norm title vector: {identifier}")
        vectors = vectors / norms[:, None]
        scores = [(task["id"], float(vectors[0] @ vector))
                  for task, vector in zip(tasks, vectors[1:], strict=True)]
        ranked = sorted(scores, key=lambda item: (-item[1], item[0]))
        rows.append(dict(id=identifier, group=group(case), expected=case["expected_target"],
                         ranked=ranked, project=candidate_labels.get(identifier)))
    return rows


def metrics(rows, threshold):
    tp = fp = fn = top3 = 0
    for row in rows:
        first, score = row["ranked"][0]
        expected = row["expected"]
        accepted = score >= threshold
        hit = accepted and expected == first
        tp += hit
        fp += accepted and not hit
        fn += expected is not None and not hit
        top3 += expected is not None and any(
            name == expected and similarity >= threshold for name, similarity in row["ranked"][:3])
    return dict(tp=tp, fp=fp, fn=fn, top3=top3,
                precision=tp / (tp + fp) if tp + fp else None,
                recall=tp / (tp + fn) if tp + fn else None,
                f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.)


def pct(number):
    return "—" if number is None else f"{number:.1%}"


def cell(result):
    return f"{pct(result['precision'])} / {pct(result['recall'])} / {result['fp']}"


def add_tables(lines, rows, title, threshold):
    lines += [f"## {title}：阈值扫描", "",
              "每格为精确率 / 召回率 / 误报数。", "",
              "| 阈值 | 正例 | 相似负例 | 跨项目同名 | 正例 + 相似负例 | 全部 |",
              "|---:|---|---|---|---|---|"]
    buckets = [[row for row in rows if row["group"] == group_name] for group_name in GROUPS]
    for point in POINTS:
        t = point / 100
        values = [cell(metrics(bucket, t)) for bucket in (*buckets, buckets[0] + buckets[1], rows)]
        lines.append(f"| {t:.2f} | " + " | ".join(values) + " |")
    lines += ["", f"在 {threshold:.2f} 下，正例组 top-3 命中 {metrics(buckets[0], threshold)['top3']}/12，"
              f"跨项目有目标的题 top-3 命中 {metrics(buckets[2], threshold)['top3']}/3。", "",
              f"## {title}：逐题完整排名与分数", "",
              "| 题号 | 预期 | 目标排名（Top-3） | 第 1 → 第 14 名（任务:余弦） |", "|---|---|---|---|"]
    for row in rows:
        target = row["expected"]
        rank = next((i for i, (name, _) in enumerate(row["ranked"], 1) if name == target), None)
        target_rank = "—" if rank is None else f"{rank}（{'是' if rank <= 3 else '否'}）"
        values = " · ".join(f"{name}:{score:.6f}" for name, score in row["ranked"])
        lines.append(f"| {row['id']} | {target or 'NONE'} | {target_rank} | {values} |")
    lines.append("")


def f1_key(stats):
    denominator = 2 * stats["tp"] + stats["fp"] + stats["fn"]
    return Fraction(2 * stats["tp"], denominator) if denominator else Fraction(0)


def select(rows):
    return max(POINTS, key=lambda point: (f1_key(metrics(rows, point / 100)), point)) / 100


def constrained(rows):
    feasible = [point for point in POINTS if metrics(rows, point / 100)["fp"] <= 2]
    if not feasible:
        return None
    return max(feasible, key=lambda point: (
        Fraction(metrics(rows, point / 100)["tp"],
                 max(1, metrics(rows, point / 100)["tp"] + metrics(rows, point / 100)["fn"])), point)) / 100


def report(cases, schemes, embedder, model_revision, *, baseline, threshold):
    flags = Counter(case["confirmed"] for case in cases)
    lines = [
        '# “可能是同一件事”提示阈值离线评测', '',
        '## 扫描前确定的选择规则', '',
        '- 三种方案各扫 0.55～0.95（含端点），步长 0.01，共 41 点。统一用全部 30 题的 top-1 F1 选阈值；F1 并列取更高阈值。用整数计数的有理数比较，避免浮点误差影响并列。',
        '- 另在全部 30 题合计误报不超过 2 次的点中选最高召回率，并列取更高阈值；没有可行点则明确报告。三组分别统计，同一全局阈值，不用分组另选点。',
        '- 跨方案推荐优先比较各自最优 F1，其次精确率、阈值；完全相同保留文本更少的方案。规则在编码和扫描调用前固定于脚本。', '',
        '## 数据与方法', '',
        f'基线提交 {baseline}。冻结合成开发集 30 个候选 × 14 个任务 × 3 种文本 = 1260 个分数，123 个方案阈值点。正例 12、相似负例 10、跨项目同名 8（其中 3 题有目标），合计 15 个有目标候选。',
        f'confirmed=false {flags[False]} 题，true {flags[True]} 题，保留草拟标签，仅作开发评测。',
        f'本地 SentenceTransformerEmbedder(local_files_only=True)，{embedder.model_name}，快照 {model_revision}；sentence-transformers {version("sentence-transformers")}、NumPy {version("numpy")}、torch {version("torch")}。HF_HUB_OFFLINE 与 TRANSFORMERS_OFFLINE 均为 1，无付费模型调用。',
        '- 文本依次为：标题；来源原文核验的规范项目名 + 标题；项目名 + 标题 + 夹具 owner。字段用一个空格连接。项目名不读取预期目标；负责人缺失则省略，不从预期标签补全。',
        '- 每题同批编码，对全池按 float64 归一化点积排序，未舍入分数降序，同分按任务 ID 升序。Top-1 ≥ 阈值才触发。错目标计 FP 和 FN；无目标触发计 FP；召回分母仅含有目标的题，负例组召回为 —。',
        '- 只测余弦，不模拟产品精确同名路径、hybrid top-k、重排或 RRF 门控。产品阈值及产品代码未改。',
        *[f'- SHA-256 {name}: {digest}' for name, digest in HASHES.items()], '',
        '复现：python -B scripts/eval_same_task_threshold.py --fixture-dir <external_v1> --output <new-report.md>（需要已缓存的模型和向量依赖）。', '',
        '## 各方案选择结果（全部 30 题）', '',
        '| 方案 | F1 最优阈值 | P / R / FP | F1 | FP≤2 的最高召回点 | P / R / FP |',
        '|---|---:|---|---:|---:|---|',
    ]
    for name, rows in schemes:
        best = select(rows)
        limit = constrained(rows)
        stats = metrics(rows, best)
        lines.append(f'| {name} | {best:.2f} | {cell(stats)} | {stats["f1"]:.1%} | '
                     + ('无可行点 | — |' if limit is None else f'{limit:.2f} | {cell(metrics(rows, limit))} |'))
    for name, rows in schemes:
        for label, point in [('F1 最优', select(rows)), ('FP≤2 最高召回', constrained(rows))]:
            lines += ['', f'### {name}：{label}', '']
            if point is None:
                lines.append('扫描范围内无可行点。')
                continue
            lines += [f'阈值 {point:.2f}。', '', '| 分组 | TP | FP | FN | P | R | F1 |', '|---|---:|---:|---:|---:|---:|---:|']
            for group_name in (*GROUPS, '全部'):
                bucket = rows if group_name == '全部' else [row for row in rows if row['group'] == group_name]
                m = metrics(bucket, point)
                lines.append(f'| {group_name} | {m["tp"]} | {m["fp"]} | {m["fn"]} | {pct(m["precision"])} | {pct(m["recall"])} | {pct(m["f1"])} |')
        add_tables(lines, rows, name, select(rows))
    name, rows = max(schemes, key=lambda scheme: (
        f1_key(metrics(scheme[1], select(scheme[1]))),
        metrics(scheme[1], select(scheme[1]))['precision'] or 0, select(scheme[1])))
    best = select(rows)
    stats = metrics(rows, best)
    lines += ['## 最终推荐', '',
              f'推荐 **{name} + {best:.2f}** 进入独立验收对比：在同一全池、相同题目和选择规则下，该组合 F1 最高（{stats["f1"]:.1%}），精确率 / 召回率 / 误报数为 {cell(stats)}。误报预算场景应另外参照上表 FP≤2 的选择，不能把 F1 最优点当作满足误报预算。',
              '这是开发集上的结论，正式采用前还需要一套独立验收题。样本少且标签未正式确认，三种文本均在同一开发集选点；加入项目与负责人也改变了输入特征，不能据此直接修改产品阈值。', '']
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = load_fixture(args.fixture_dir)
    pool = json.loads(files["pool.json"])
    cases = [json.loads(line) for line in files["cases.jsonl"].splitlines() if line.strip()]
    tasks = pool["tasks"]
    task_ids = {task["id"] for task in tasks}
    if (len(task_ids) != len(tasks) or len(tasks) != 14 or len({case["case_id"] for case in cases}) != 30
            or len(cases) != 30 or Counter(map(group, cases)) != dict(zip(GROUPS, (12, 10, 8)))
            or any(case["workspace_id"] != pool["workspace_id"] or case["candidate"]["status"] != "pending"
                   or (case["expected_target"] is not None and case["expected_target"] not in task_ids) for case in cases)
            or any(task["workspace_id"] != pool["workspace_id"] for task in tasks)):
        raise ValueError("Unexpected frozen fixture structure")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from huggingface_hub import hf_hub_download
    from linkloom.indexing import SentenceTransformerEmbedder
    from linkloom.tasks.same_task_hints import SameTaskHintConfig

    embedder = SentenceTransformerEmbedder(local_files_only=True)
    config = Path(hf_hub_download(embedder.model_name, "config.json", local_files_only=True))
    threshold = SameTaskHintConfig().min_title_similarity
    baseline = subprocess.check_output(["git", "merge-base", "HEAD", "origin/master"], cwd=ROOT, text=True).strip()
    projects = project_labels(pool, cases)
    base = evaluate(embedder, tasks, cases)
    augmented = evaluate(embedder, tasks, cases, projects)
    with_owners = evaluate(embedder, tasks, cases, projects, owners=True)
    load_fixture(args.fixture_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8", newline="\n") as stream:
        stream.write(report(cases, [("只比标题", base), ("标题+项目名", augmented),
                                    ("标题+项目名+负责人", with_owners)], embedder, config.parent.name,
                            baseline=baseline, threshold=threshold))
    print(args.output)


if __name__ == "__main__":
    main()
