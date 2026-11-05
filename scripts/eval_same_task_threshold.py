"""Offline Same Task Hint title-cosine sweep for the frozen external dev v1."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
HASHES = {
    "pool.json": "df6244fa6dd95673017236a2c39c644dc937e97e8f955581d41b70a989831234",
    "cases.jsonl": "dfab5fc3f8affd6b8b191f6ba553562868246723e89c1522fa702eff32efba76",
    "README.md": "b4c4016677bacfc9df64cd6ec28d06520ccf0ee5e10f883c1fea1d867b39a1b3",
}
GROUPS = ("正例", "相似负例", "跨项目同名")
POINTS = range(70, 96)


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


def evaluate(embedder, tasks, cases, projects=None):
    import numpy as np

    task_labels, candidate_labels = projects or ({}, {})
    def text(title, label):
        return f"{label} {title}" if label else title

    task_texts = [text(task["title"], task_labels.get(task["id"])) for task in tasks]
    rows = []
    for case in cases:
        identifier = case["case_id"]
        texts = [text(case["candidate"]["title"], candidate_labels.get(identifier)), *task_texts]
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


def report(cases, base, augmented, embedder, model_revision, *, baseline, threshold):
    core = [row for row in base if row["group"] != GROUPS[2]]
    best = max(POINTS, key=lambda t: (metrics(core, t / 100)["f1"], t)) / 100
    current, best_stats = metrics(core, threshold), metrics(core, best)
    cross_left = [row for row in base if row["group"] == GROUPS[2]]
    cross_right = [row for row in augmented if row["group"] == GROUPS[2]]
    flags = Counter(case["confirmed"] for case in cases)
    lines = [
        "# “可能是同一件事”提示阈值离线评测", "",
        f"基线提交 {baseline}。冻结开发集 30 个候选 × 14 个任务，两种文本共 840 个分数。",
        f"原件中的 confirmed 为 false 的题有 {flags[False]} 个，为 true 的题有 {flags[True]} 个。"
        "按用户指定哈希对原有预期做开发评测，保留原字段，结果按草拟标签解释。", "",
        f"建议在下一组独立验收题上试 **{best:.2f}**。选点只用 12 道正例和 10 道相似负例最大化 top-1 F1，"
        "同分取较高阈值。跨项目题单列。",
        f"这 22 题在 {best:.2f} 的精确率、召回率、误报数为 {cell(best_stats)}，F1 {best_stats['f1']:.1%}；"
        f"当前 {threshold:.2f} 为 {cell(current)}，F1 {current['f1']:.1%}。"
        + (" 最优点在扫描下边界，本次未评估更低阈值。" if best == min(POINTS) / 100 else ""), "",
        f"在 {threshold:.2f} 下，跨项目组的精确率、召回率、误报数从标题模式的 "
        f"{cell(metrics(cross_left, threshold))} 变为加项目名后的 {cell(metrics(cross_right, threshold))}。"
        "标题完全相同时，两份文档的输入相同，余弦本身无法区分。加项目名只是相似度特征，没有做项目硬过滤。",
        "当前产品另有精确同名路径，独立于这个阈值；本实验不模拟该路径或生产提示总量。", "",
        "## 方法", "",
        f"- 复用 SentenceTransformerEmbedder(local_files_only=True)，模型 {embedder.model_name}，"
        f"本地快照 {model_revision}，sentence-transformers {version('sentence-transformers')}、"
        f"NumPy {version('numpy')}、torch {version('torch')}。每题同批编码，采用产品的 float64 归一化点积。",
        "- 基线比较标题原样。对照把来源原文唯一可定位的规范项目名放在标题前，以一个空格连接。"
        "两者均扫同一工作区全池。比较文本不使用负责人、截止或预期标签。",
        "- 按未舍入的余弦降序，同分按任务 ID 升序。产品实际提示还经过 hybrid top-k、"
        "重排或 RRF 门控；本报告只扫标题余弦。输出保留六位小数。",
        "- Top-1 分数 ≥ 阈值才触发。错目标同时记一次 FP 和一次 FN，无目标出现任一过阈值则记一次 FP。"
        "精确率或召回率无分母记「—」。逐题 Top-3 只看排名，分组 top-3 另要求过阈值。",
        "- 本地离线执行。夹具 SHA-256：",
        *[f"  - {name} {digest}" for name, digest in HASHES.items()], "",
        "复现命令：python -B scripts/eval_same_task_threshold.py --fixture-dir <external_v1> --output <new-report.md>。"
        "需要已缓存的同一向量模型和项目向量依赖。", "",
    ]
    add_tables(lines, base, "标题", threshold)
    add_tables(lines, augmented, "项目名 + 标题", threshold)
    lines += ["## 跨项目同名逐题对照", "",
              f"按 {threshold:.2f} 判 top-1，最高分近似并列用 1e-7 绝对误差标记。", "",
              "| 题号 | 来源项目 | 预期 | 只比标题最高分（并列集合） | 加项目最高分（并列集合） | 标题 → 加项目 |",
              "|---|---|---|---|---|---|"]
    def tied(row):
        maximum = row["ranked"][0][1]
        ids = [name for name, value in row["ranked"] if math.isclose(value, maximum, rel_tol=0., abs_tol=1e-7)]
        return f"{maximum:.6f} ({', '.join(ids)})"
    def outcome(row):
        stats = metrics([row], threshold)
        return "误报" if stats["fp"] else "命中" if stats["tp"] else "漏答" if stats["fn"] else "不触发"

    for left, right in zip(cross_left, cross_right, strict=True):
        lines.append(f"| {left['id']} | {right['project']} | {left['expected'] or 'NONE'} | "
                     f"{tied(left)} | {tied(right)} | {outcome(left)} → {outcome(right)} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "work/same-task-threshold.md")
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
    load_fixture(args.fixture_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf8", newline="\n") as stream:
        stream.write(report(cases, base, augmented, embedder, config.parent.name,
                            baseline=baseline, threshold=threshold))
    print(args.output)


if __name__ == "__main__":
    main()
