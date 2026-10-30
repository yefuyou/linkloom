"""Held-out three-system wiring around the unchanged, frozen AMI harness.

No extraction prompts, product rules, or gold content are changed here.
All prediction files are completed before finish_evaluation opens the gold.
"""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
import eval_tasks as ev

MEETINGS = tuple(f"ES2003{part}" for part in "abcd")
GOLD_SHA256 = "fdb653c84c48cfe9041112352614c80fcf0a79e18777ad70d7314150ecaec7b8"
FROZEN_COMMIT = "a8dabf40d9a2613781ded1f55747bb6a8e5a81c2"
SYSTEMS = ("baseline", "legacy", "v2")
LABELS = {"baseline": "基线", "legacy": "legacy（接线修复后）", "v2": "meeting_v2（冻结版）"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp():
    return datetime.now(UTC).isoformat()


def verify_freeze(gold_path):
    """Hash bytes only; do not parse or display any gold text before prediction."""
    manifest = json.loads((ROOT / "benchmarks/ami_tasks/v2_frozen.json").read_text(encoding="utf-8"))
    checks = {}
    for name, expected in manifest["source_normalized_sha256"].items():
        current = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        frozen = subprocess.check_output(["git", "show", f"{FROZEN_COMMIT}:{name}"], cwd=ROOT).replace(b"\r\n", b"\n")
        if sha(current) != expected or current != frozen:
            raise ValueError(f"STOP: frozen source mismatch: {name}")
        checks[name] = sha(current)
    gold_hash = sha(gold_path.read_bytes())
    if gold_hash != GOLD_SHA256:
        raise ValueError("STOP: ES2003 gold hash mismatch")
    return {"checked_at": timestamp(), "frozen_commit": FROZEN_COMMIT,
            "source_normalized_sha256": checks, "gold_sha256": gold_hash,
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}


def load_inputs(data, archive_path):
    """Only transcript files and official participant metadata; never labels/CSV."""
    expected = (ROOT / "benchmarks/ami_tasks/ami_public_manual_1.6.2.zip.sha256").read_text(encoding="ascii").split()[0]
    with archive_path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError("AMI archive checksum mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        names = [n for n in archive.namelist() if n.endswith("corpusResources/meetings.xml")]
        if len(names) != 1:
            raise ValueError("Missing/ambiguous official meetings metadata")
        raw = archive.read(names[0])
        root = ev.ET.fromstring(raw)
    meetings = []
    for mid in MEETINGS:
        meeting = json.loads((data / "transcripts" / f"{mid}.json").read_text(encoding="utf-8"))
        if meeting["meeting_id"] != mid:
            raise ValueError("Transcript meeting ID mismatch")
        speakers = ev.speaker_metadata(root, mid)
        if meeting["speakers"] != speakers:
            raise ValueError("Prepared transcript roster differs from official AMI metadata")
        meetings.append({**meeting, "speakers": speakers})
    return meetings, {"archive_sha256": expected, "member": names[0], "member_sha256": sha(raw),
                       "rosters": {m["meeting_id"]: m["speakers"] for m in meetings}}


def generate_all(meetings, out, client):
    predictions = {m["meeting_id"]: {} for m in meetings}
    diagnostics = {m["meeting_id"]: {} for m in meetings}
    for system in SYSTEMS:
        target = out / "prediction_runs" / system
        target.mkdir(parents=True)
        client.output_dir = target
        # The legacy adapter keeps its original 32 KiB cap. Only v2 receives
        # the trusted Host's frozen 192 KiB configuration.
        client.gemini_max_input_bytes = 192 * 1024 if system == "v2" else 32768
        client._gemini = None
        for meeting in meetings:
            mid = meeting["meeting_id"]
            if system == "baseline":
                items = ev.baseline_predict(meeting, client)
                detail = []
            else:
                items, detail = ev.linkloom_predict(meeting, client, speaker_wiring=True,
                    task_extraction_mode="meeting_v2" if system == "v2" else "legacy")
            predictions[mid][system], diagnostics[mid][system] = items, detail
            ev.new_json(target / f"{mid}.predictions.json", {"predictions": items, "diagnostics": detail})
            print(json.dumps({"stage": "prediction", "system": system, "meeting_id": mid,
                              "predictions": len(items), "requests": client.counter.requests}), flush=True)
    ev.new_json(out / "predictions.json", {"model": ev.GEMINI, "predictions": predictions,
                                          "diagnostics": diagnostics, "completed_at": timestamp()})
    return predictions, diagnostics


def sample_rows(rows, seed=20261029):
    sampled = []
    for system in SYSTEMS:
        subset = sorted((r for r in rows if r["system"] == system),
                        key=lambda r: (r["meeting_id"], r["prediction_id"], r["gold_id"] or ""))
        sampled.extend(random.Random(f"{seed}:{system}").sample(subset, math.ceil(len(subset) * 0.2)))
    return sampled


def score_all(meetings, gold_path, out, client, predictions, diagnostics):
    if not (out / "predictions.json").is_file():
        raise ValueError("Cannot open gold before all prediction files are complete")
    pair_reports, all_rows = {}, []
    for system in ("legacy", "v2"):
        target = out / f"judge_{system}"
        target.mkdir()
        client.output_dir = target
        client.evaluation_scope = ({"task_extraction_mode": "meeting_v2",
                                   "windows": sum(len(diagnostics[m["meeting_id"]][system]) for m in meetings)}
                                  if system == "v2" else {})
        pair_predictions = {mid: {"baseline": items["baseline"], "linkloom": items[system]}
                            for mid, items in predictions.items()}
        # Same opaque prediction IDs, prompt, schemas, validator, field overrides,
        # and metrics as the frozen harness. Reuse baseline judgments in pair 2.
        ev.finish_evaluation(meetings, gold_path, target, client, pair_predictions,
                             saved_baseline=out / "judge_legacy" if system == "v2" else None)
        pair_reports[system] = json.loads((target / "results.json").read_text(encoding="utf-8"))
        rows = json.loads((target / "judgments.json").read_text(encoding="utf-8"))
        all_rows.extend({**row, "system": system if row["system"] == "linkloom" else "baseline"}
                        for row in rows if system == "legacy" or row["system"] != "baseline")
    report = pair_reports["v2"]
    report["micro"] = {"baseline": pair_reports["legacy"]["micro"]["baseline"],
                       "legacy": pair_reports["legacy"]["micro"]["linkloom"],
                       "v2": pair_reports["v2"]["micro"]["linkloom"]}
    report["per_meeting"] = {mid: {"baseline": pair_reports["legacy"]["per_meeting"][mid]["baseline"],
                                    "legacy": pair_reports["legacy"]["per_meeting"][mid]["linkloom"],
                                    "v2": pair_reports["v2"]["per_meeting"][mid]["linkloom"]} for mid in MEETINGS}
    report["evaluation_scope"] = {"dataset": "ES2003a-d held-out final evaluation", "systems": SYSTEMS,
                                    "all_predictions_saved_before_gold_parse": True,
                                    "predictions_file_sha256": sha((out / "predictions.json").read_bytes())}
    sampled = sample_rows(all_rows)
    report["spot_check"] = {"seed": 20261029, "population": len(all_rows), "sampled": len(sampled),
                            "method": "ceil(20%) independently per system; predictions plus unmatched gold"}
    ev.new_json(out / "judgments.json", all_rows)
    ev.write_spot_check(out / "spot_check.csv", sampled)
    return report, all_rows


def write_report(path, report, rows):
    def fraction(count):
        n, d = count["numerator"], count["denominator"]
        return f"{n}/{d}（{n/d:.1%}）" if d else f"{n}/{d}（不适用）"
    fields = [("待办准确率", "precision"), ("待办召回率", "recall"),
              ("负责人正确率（匹配任务）", "owner_accuracy_on_matched"),
              ("截止时间正确率（匹配且 gold 明确期限）", "deadline_accuracy_on_matched_stated_gold"),
              ("截止时间端到端正确率（全部明确期限 gold）", "deadline_end_to_end_accuracy_on_stated_gold"),
              ("讨论误判数 / 全部预测", "discussion_false_positives")]
    lines = ["# ES2003 冻结版最终评测", "", "本轮裁判结果尚待人工抽查。标准答案及 v2 均在运行前冻结；未根据本轮结果修改。", "",
             f"运行起点：`{report['freeze_verification']['head']}`；冻结提交：`{FROZEN_COMMIT}`。",
             f"gold SHA-256：`{GOLD_SHA256}`。运行前只计算 gold 字节哈希，全部预测保存后才解析 gold 供裁判使用。",
             f"模型：{ev.GEMINI}，thinking_level={ev.GEMINI_THINKING}，输出上限 {ev.GEMINI_MAX_OUTPUT} token（包含思考和 JSON）；裁判 {ev.DEEPSEEK}。配置和匹配规则沿用冻结脚本。", "",
             "| 指标 | 基线 | legacy（接线修复后） | v2 |", "| --- | ---: | ---: | ---: |"]
    for label, key in fields:
        lines.append("| " + " | ".join([label, *[fraction(report["micro"][s]["metric_counts"][key]) for s in SYSTEMS]]) + " |")
    lines += ["", "准确率分母为预测数，召回率分母为全部 gold；负责人只在任务匹配后计分。截止时间只在 gold 明确期限的条目计分，同时列出匹配子集和端到端分母；未说明／无法确定单独统计，不算正确或编造。讨论误判行分子为数量。", "",
              "负责人代词未解析的一律判错；说话人编号按本场 AMI 元数据映射为角色。原文明确指向同一次下次会议的相对时长与事件表述等价，不能据会议技术时间锚推断日历日期。", "",
              "## AMI 角色映射", "", f"来源：官方 AMI ZIP 的 `{report['ami_metadata']['member']}`，成员 SHA-256 `{report['ami_metadata']['member_sha256']}`。本轮重新读取，与四场预处理转录中的映射逐项核对一致。", "",
              "| 会议 | A | B | C | D |", "| --- | --- | --- | --- | --- |"]
    for mid, roster in report["ami_metadata"]["rosters"].items():
        mapping = {s["speaker"]: f"{s['role']} ({s['participant_id']})" for s in roster}
        lines.append("| " + " | ".join([mid, *[mapping[s] for s in "ABCD"]]) + " |")
    for system in SYSTEMS:
        metric = report["micro"][system]
        lines += ["", f"## {LABELS[system]}逐项差异", "", f"TP={metric['tp']}，FP={metric['fp']}，FN={metric['fn']}；截止时间弃答 {metric['deadline_prediction_abstentions']}/{metric['tp']+metric['fp']}；匹配且有期限 gold 上弃答 {metric['deadline_abstentions_on_matched_stated_gold']}/{metric['deadline_stated_matched_count']}。", "", "漏掉的任务：", ""]
        missed = [r for r in rows if r["system"] == system and r["category"] == "FN"]
        lines.extend(f"- {r['meeting_id']} / {r['gold_id']}：{json.loads(r['gold'])['task']}；中文：{r['gold_task_zh']}。" for r in missed)
        if not missed:
            lines.append("无。")
        lines += ["", "多出的预测：", ""]
        extras = [r for r in rows if r["system"] == system and r["category"] not in {"FN", "MATCH"}]
        lines.extend(f"- {r['meeting_id']} / {r['prediction_id']} [{r['category']}]：{json.loads(r['prediction'])['task']}；中文：{r['prediction_task_zh']}。裁判理由：{r['reason']}" for r in extras)
        if not extras:
            lines.append("无。")
    lines += ["", "## 请求、费用与抽查", "", f"请求 {report['counter']['requests']}/80；按 API 实际 token 用量和沿用费率计算 ${report['actual_cost_usd_from_usage']:.9f}/$1.50（用量估算，不是账户账单）。失败请求的未知费用保守保留。", "",
              "| 模型 | 请求 | 输入 token | 输出 token（含思考） | 费用 USD |", "| --- | ---: | ---: | ---: | ---: |"]
    for model, usage in report["usage_by_model"].items():
        lines.append(f"| {model} | {usage['requests']} | {usage['input_tokens']} | {usage['output_tokens_including_thinking']} | {usage['cost_usd_from_usage']:.9f} |")
    lines += ["", f"抽查固定种子 20261029，各系统分别向上取整抽 20%，共 {report['spot_check']['sampled']}/{report['spot_check']['population']} 条。包括预测判断与未匹配 gold，附中文翻译、裁判理由及空白人工判断列。", "",
              "## 执行与边界", "", "一次正式运行；未自动重试或择优选结果。所有系统的全部预测完成并保存之后，才读取标准答案进行评分。预测输入仅来自转录及原始角色元数据；候选 CSV、AMI 摘要和 gold 未进入被评测系统。baseline 整场读取；legacy 保持 32 KiB 适配器上限和发言接线；v2 使用冻结的可信 Host 192 KiB 上限。", "",
              "未修改任何冻结源码、提示词、规则、阈值和 gold。ES2002 开发集结果不在本轮重跑。完整原始响应、转录和中间结果留在忽略的 work/；仓库只收结果、必要的抽查引用和本轮评测接线。", "",
              "AMI Meeting Corpus / AMI Project / AMI Consortium；来源 https://groups.inf.ed.ac.uk/ami/corpus/ ，CC BY 4.0：https://creativecommons.org/licenses/by/4.0/ 。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(data, archive, gold, out, *, live=False):
    check = verify_freeze(gold)
    if not live:
        raise ValueError("No calls made: explicit --live required")
    meetings, metadata = load_inputs(data, archive)
    out.mkdir(parents=True, exist_ok=False)
    ev.new_json(out / "freeze_verification.json", check)
    client = ev.Client(ev.Counter(80, 1.5), live=True)
    try:
        predictions, diagnostics = generate_all(meetings, out, client)
        report, rows = score_all(meetings, gold, out, client, predictions, diagnostics)
        report.update({"freeze_verification": check, "ami_metadata": metadata,
                       "completed_at": timestamp(), "attempts": 1})
        if verify_freeze(gold)["source_normalized_sha256"] != check["source_normalized_sha256"]:
            raise ValueError("Frozen source changed during evaluation")
        ev.new_json(out / "results.json", report)
        write_report(out / "results.md", report, rows)
    except Exception as error:
        ev.new_json(out / "failure.json", {"error_type": type(error).__name__, "message": str(error),
            "counter": client.counter.summary(), "actual_cost_usd_from_usage": client.actual_cost_usd,
            "usage_by_model": client.usage_by_model, "failed_at": timestamp()})
        raise
    finally:
        print(json.dumps({"counter": client.counter.summary(),
                          "actual_cost_usd_from_usage": client.actual_cost_usd}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "archive", "gold", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    run(args.data, args.archive, args.gold, args.out, live=args.live)
