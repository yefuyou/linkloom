"""Held-out three-system wiring around the unchanged, frozen AMI harness.

No extraction prompts, product rules, or gold content are changed here.
All prediction files are completed before finish_evaluation opens the gold.
"""
from __future__ import annotations

import argparse
import copy
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
PREDICTIONS_SHA256 = "7b3cd5a55dbecc732025a2fd9adeee934924b8f3e1e9c82c4cb9d0000d65c387"
INVALID_TEXT_SHA256 = "745e83eedf046d8a432cdce5a22452efaf4eaae01e6be0a0f6b6d48de0dfa115"


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


def sample_rows(rows, seed=20261029, *, required=None):
    sampled = []
    for system in SYSTEMS:
        subset = sorted((r for r in rows if r["system"] == system),
                        key=lambda r: (r["meeting_id"], r["prediction_id"], r["gold_id"] or ""))
        sampled.extend(random.Random(f"{seed}:{system}").sample(subset, math.ceil(len(subset) * 0.2)))
    if required and not any((r["system"], r["meeting_id"]) == required and r["prediction_id"] for r in sampled):
        candidates = sorted((r for r in rows if (r["system"], r["meeting_id"]) == required and r["prediction_id"]),
                            key=lambda r: r["prediction_id"])
        if not candidates:
            raise ValueError("Required rejudged meeting has no judgment to sample")
        chosen = random.Random(f"{seed}:required:{required}").choice(candidates)
        # Keep the per-system ceil(20%) size; constrain one randomly selected
        # slot to the rejudged meeting instead of increasing the sample count.
        slots = [i for i, r in enumerate(sampled) if r["system"] == required[0]]
        slot = random.Random(f"{seed}:replacement:{required}").choice(slots)
        sampled[slot] = chosen
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
    required = getattr(client, "required_review", None)
    sampled = sample_rows(all_rows, required=required)
    report["spot_check"] = {"seed": 20261029, "population": len(all_rows), "sampled": len(sampled),
                            "method": "ceil(20%) independently per system; predictions plus unmatched gold"}
    if required:
        report["spot_check"].update({"required_rejudged_meeting": list(required),
            "method": "ceil(20%) per system, fixed-seed random sample constrained to include an actual legacy ES2003a rejudgment",
            "rejudged_csv_data_rows": [i + 1 for i, r in enumerate(sampled)
                if (r["system"], r["meeting_id"]) == required and r["prediction_id"]]})
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
    if report.get("continuation"):
        continuation = report["continuation"]
        lines += ["", "## 首次中止与唯一一次裁判续跑", "",
            "首次运行在 ES2003a / legacy 裁判处中止：两条预测 p-145d7d661374aae6 和 p-752e663f76945fc4 都被判为 MATCH，且均指向 ES2003a-c001，违反同一 gold 最多匹配一条预测的既定规则。首次结果见 [中止报告](results_es2003_attempt1.md)。",
            f"无效响应原文完整保存在 [裁判响应原文](judge_es2003a_legacy_invalid_response.txt)，UTF-8 内容 SHA-256 为 `{INVALID_TEXT_SHA256}`。原始提供商响应封装仍在被忽略的首次运行目录中。",
            "用户随后明确授权只续跑裁判一次。重判提示仅追加 ES2003a 当前响应的校验错误、冲突的预测 ID/gold ID 和既有一对一规则原文，没有发送其他会议的判断结果。所有预测和已通过校验的 ES2003a 基线裁判响应均复用；没有新增 Gemini 调用。重判及余下判断均通过同一个冻结校验器，未自动修复裁判结论。", "",
            "| 执行 | 状态 | 请求数 | 费用 USD |", "| --- | --- | ---: | ---: |",
            f"| 首次运行 | 裁判不合规，中止 | {continuation['prior_requests']} | {continuation['prior_cost_usd']:.9f} |",
            f"| 唯一一次裁判续跑 | 完成 | {report['counter']['requests'] - continuation['prior_requests']} | {report['actual_cost_usd_from_usage'] - continuation['prior_cost_usd']:.9f} |",
            f"| 累计 | 完成 | {report['counter']['requests']} | {report['actual_cost_usd_from_usage']:.9f} |", "",
            f"抽查保证包含 ES2003a / legacy 的实际重判判断，位于 CSV 数据行 {report['spot_check']['rejudged_csv_data_rows']}（不含表头）。固定随机种子，在该系统样本内置换一个位置满足此条件，总样本量保持各系统向上取整的 20%。"]
    lines += ["", f"抽查固定种子 20261029，各系统分别向上取整抽 20%，共 {report['spot_check']['sampled']}/{report['spot_check']['population']} 条。包括预测判断与未匹配 gold，附中文翻译、裁判理由及空白人工判断列。", "",
              "## 执行与边界", "", "全部预测只生成一次；未自动重试或择优选结果。裁判续跑仅限报告中明确披露的授权范围。所有系统的全部预测完成并保存之后，才读取标准答案进行评分。预测输入仅来自转录及原始角色元数据；候选 CSV、AMI 摘要和 gold 未进入被评测系统。baseline 整场读取；legacy 保持 32 KiB 适配器上限和发言接线；v2 使用冻结的可信 Host 192 KiB 上限。", "",
              "未修改冻结的 v2 源码、提示词、规则、阈值和 gold，原评测脚本也未修改。ES2002 开发集结果不在本轮重跑。完整原始响应、转录和中间结果留在忽略的 work/；仓库只收结果、必要的抽查引用和本轮评测接线。", "",
              "AMI Meeting Corpus / AMI Project / AMI Consortium；来源 https://groups.inf.ed.ac.uk/ami/corpus/ ，CC BY 4.0：https://creativecommons.org/licenses/by/4.0/ 。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class JudgeContinuationClient(ev.Client):
    """A single judge-only continuation; no extraction route or inner retries."""

    def __init__(self, prior, source, predictions):
        counter = ev.Counter(80, 1.5, requests=prior["counter"]["requests"],
                             usd_upper_bound=prior["counter"]["reserved_upper_bound_usd"])
        super().__init__(counter, live=True)
        self.actual_cost_usd = prior["actual_cost_usd_from_usage"]
        self.usage_by_model = copy.deepcopy(prior["usage_by_model"])
        self.source, self.predictions = source, predictions
        self.plan = [(mid, system) for mid in MEETINGS for system in ("baseline", "legacy")]
        self.plan += [(mid, "v2") for mid in MEETINGS]
        self.position = 0
        self.required_review = ("legacy", "ES2003a")

    def complete(self, request):
        raise RuntimeError("Judge-only continuation cannot call an evaluated model")

    def gemini_adapter(self):
        raise RuntimeError("Judge-only continuation cannot initialize Gemini")

    def generate(self, model, prompt, schema, max_output=4096):
        if model != ev.DEEPSEEK or self.position >= len(self.plan):
            raise ValueError("Unexpected provider call in judge-only continuation")
        mid, system = self.plan[self.position]
        data = json.JSONDecoder().raw_decode(prompt.split("\nDATA:\n", 1)[1])[0]
        rng = random.Random(f"20261029:{mid}:{'baseline' if system == 'baseline' else 'linkloom'}")
        expected = [{"prediction_id": f"p-{rng.getrandbits(64):016x}", **p} for p in self.predictions[mid][system]]
        if (data["meeting"]["meeting_id"] != mid
            or sorted(data["predictions"], key=lambda p: p["prediction_id"]) != sorted(expected, key=lambda p: p["prediction_id"])
            or schema != ev.REVIEW_JUDGE_SCHEMA or max_output != 32768):
            raise ValueError("Continuation request differs from the frozen scoring protocol")
        self.position += 1
        if (mid, system) == ("ES2003a", "baseline"):
            return json.loads((self.source / "judge_legacy/ES2003a.baseline.judge.json").read_text(encoding="utf-8"))
        if (system, mid) == self.required_review:
            feedback = {"meeting_id": mid, "validation_error": "Invalid or many-to-one match",
                "conflicting_prediction_ids": ["p-145d7d661374aae6", "p-752e663f76945fc4"],
                "conflicting_gold_id": "ES2003a-c001",
                "violated_rule": "one prediction matches at most one gold task and each gold task at most once",
                "instruction": "Your previous response matched both listed predictions to the same gold task. Rejudge this CURRENT meeting under the SAME rules and return the SAME schema. Do not change predictions or gold. Use the existing maximum-valid-matches and tie-breaking rules."}
            prompt += "\nVALIDATION_ERROR_FOR_CURRENT_MEETING_ONLY:\n" + ev.encoded(feedback).decode()
            ev.new_json(self.output_dir / "ES2003a.legacy.rejudge-feedback.json", feedback)
        # No catch or retry. A malformed replacement is rejected by the frozen
        # validator before finish_evaluation can advance to the next meeting.
        return super().generate(model, prompt, schema, max_output)


def resume_judges(data, archive, gold, source, out, *, live=False):
    current_check = verify_freeze(gold)
    if not live:
        raise ValueError("No calls made: explicit --live required")
    prior = json.loads((source / "failure.json").read_text(encoding="utf-8"))
    if prior["counter"]["requests"] != 28 or prior["message"] != "Invalid or many-to-one match":
        raise ValueError("Only the authorized first-attempt failure can be continued")
    saved_bytes = (source / "predictions.json").read_bytes()
    if sha(saved_bytes) != PREDICTIONS_SHA256:
        raise ValueError("Saved predictions changed; continuation denied")
    saved = json.loads(saved_bytes)
    if set(saved["predictions"]) != set(MEETINGS) or any(set(v) != set(SYSTEMS) for v in saved["predictions"].values()):
        raise ValueError("Incomplete original predictions; no extraction rerun is allowed")
    raw = json.loads((source / "judge_legacy/provider-response-0028.json").read_text(encoding="utf-8"))
    invalid_text = raw["choices"][0]["message"]["content"]
    if sha(invalid_text.encode("utf-8")) != INVALID_TEXT_SHA256:
        raise ValueError("Original invalid response changed")
    meetings, metadata = load_inputs(data, archive)
    out.mkdir(parents=True, exist_ok=False)
    (out / "predictions.json").write_bytes(saved_bytes)
    (out / "judge_es2003a_legacy_invalid_response.txt").write_bytes(invalid_text.encode("utf-8"))
    original_check = json.loads((source / "freeze_verification.json").read_text(encoding="utf-8"))
    ev.new_json(out / "freeze_verification.json", current_check)
    continuation = {"authorized": "one judge-only continuation; stop immediately on invalid replacement",
        "prior_requests": prior["counter"]["requests"], "prior_cost_usd": prior["actual_cost_usd_from_usage"],
        "resume_head": current_check["head"], "predictions_sha256": PREDICTIONS_SHA256,
        "invalid_response_text_sha256": INVALID_TEXT_SHA256, "started_at": timestamp()}
    ev.new_json(out / "continuation.json", continuation)
    client = JudgeContinuationClient(prior, source, saved["predictions"])
    try:
        report, rows = score_all(meetings, gold, out, client, saved["predictions"], saved["diagnostics"])
        if client.position != len(client.plan):
            raise ValueError("Judge continuation did not complete the planned judgments")
        report.update({"freeze_verification": original_check, "ami_metadata": metadata,
            "completed_at": timestamp(), "attempts": 2, "continuation": continuation})
        verify_freeze(gold)
        ev.new_json(out / "results.json", report)
        write_report(out / "results.md", report, rows)
    except Exception as error:
        ev.new_json(out / "failure.json", {"error_type": type(error).__name__, "message": str(error),
            "counter": client.counter.summary(), "actual_cost_usd_from_usage": client.actual_cost_usd,
            "usage_by_model": client.usage_by_model, "failed_at": timestamp(), "continuation": continuation})
        raise
    finally:
        print(json.dumps({"counter": client.counter.summary(),
                          "actual_cost_usd_from_usage": client.actual_cost_usd}), flush=True)


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
    parser.add_argument("--resume-judges-from", type=Path, help="Only with explicit authorization; reuse every prediction and carry first-attempt spend")
    args = parser.parse_args()
    if args.resume_judges_from:
        resume_judges(args.data, args.archive, args.gold, args.resume_judges_from, args.out, live=args.live)
    else:
        run(args.data, args.archive, args.gold, args.out, live=args.live)
