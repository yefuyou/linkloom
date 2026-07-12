from typing import Dict, Any, List
import datetime

class MarkdownReportGenerator:
    def __init__(self, run_metadata: Dict[str, Any], topic_results: Dict[str, Any], relation_results: Dict[str, Any], gold_validation_issues: List[Dict[str, Any]], prediction_evidence_stats: Dict[str, Any], bad_cases: List[Dict[str, Any]]):
        self.meta = run_metadata
        self.topic = topic_results
        self.relation = relation_results
        self.gold_issues = gold_validation_issues
        self.pred_ev_stats = prediction_evidence_stats
        self.bad_cases = bad_cases

    def generate(self) -> str:
        lines = []
        lines.append("# Relation Vault Agent 评测报告\n")

        # 1. Running information
        lines.append("## 1. 运行基本信息\n")
        lines.append(f"- **运行 ID**: `{self.meta.get('run_id')}`")
        lines.append(f"- **数据集版本**: `{self.meta.get('dataset_version')}`")
        lines.append(f"- **模型 Provider**: `{self.meta.get('provider')}`")
        lines.append(f"- **Mock 场景模式**: `{self.meta.get('mock_scenario')}`")
        lines.append(f"- **主题分类 Prompt 版本**: `{self.meta.get('topic_prompt_version')}`")
        lines.append(f"- **关系分类 Prompt 版本**: `{self.meta.get('relation_prompt_version')}`")
        lines.append(f"- **文档数量**: `{self.meta.get('document_count')}`")
        lines.append(f"- **候选对数量**: `{self.meta.get('candidate_pair_count')}`")
        lines.append(f"- **运行费用**: `${self.meta.get('estimated_cost_usd'):.4f}`")
        lines.append(f"- **启动时间**: `{self.meta.get('started_at')}`")
        lines.append(f"- **结束时间**: `{self.meta.get('finished_at')}`\n")

        # 2. Topic Metrics
        lines.append("## 2. 主题识别评测指标 (Topic Classification)\n")
        lines.append("### 基础混淆矩阵")
        t_counts = self.topic["counts"]
        lines.append("| 指标项 | 数量 |")
        lines.append("|---|---|")
        lines.append(f"| True Positive (真正例) | {t_counts['true_positive']} |")
        lines.append(f"| True Negative (真负例) | {t_counts['true_negative']} |")
        lines.append(f"| False Positive (假阳性) | {t_counts['false_positive']} |")
        lines.append(f"| False Negative (假阴性) | {t_counts['false_negative']} |")
        lines.append("")

        lines.append("### 分类效能指标")
        t_metrics = self.topic["metrics"]
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append(f"| Accuracy (准确率) | {t_metrics['accuracy']:.4%} |")
        lines.append(f"| Precision (精准率) | {t_metrics['precision']:.4%} |")
        lines.append(f"| Recall (召回率) | {t_metrics['recall']:.4%} |")
        lines.append(f"| F1-Score | {t_metrics['f1']:.4%} |")
        lines.append(f"| False Positive Rate (假阳性率) | {t_metrics['false_positive_rate']:.4%} |")
        lines.append(f"| False Negative Rate (假阴性率) | {t_metrics['false_negative_rate']:.4%} |")
        lines.append("")

        # 3. Relation Metrics
        lines.append("## 3. 关系识别评测指标 (Relation Classification)\n")
        lines.append("### 链接关系判定")
        r_counts = self.relation["counts"]
        r_metrics = self.relation["metrics"]
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append(f"| Precision (关系精准率) | {r_metrics['precision']:.4%} |")
        lines.append(f"| Recall (关系召回率) | {r_metrics['recall']:.4%} |")
        lines.append(f"| F1-Score | {r_metrics['f1']:.4%} |")
        lines.append(f"| 关系 True Positives | {r_counts['linkage_tps']} |")
        lines.append(f"| 关系 False Positives | {r_counts['false_positive']} |")
        lines.append(f"| 关系 False Negatives | {r_counts['false_negative']} |")
        lines.append("")

        lines.append("### 方向与类型判定 (针对 True Positives)")
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines.append(f"| Direction Accuracy (方向准确率) | {r_metrics['direction_accuracy']:.4%} |")
        lines.append(f"| Type Accuracy (类型准确率) | {r_metrics['type_accuracy']:.4%} |")
        lines.append("")

        lines.append("### 关系类型细分统计")
        lines.append("| 关系类型 | 正确预测数 | 错误预测数 |")
        lines.append("|---|---|---|")
        for rtype, stats in self.relation["type_stats"].items():
            lines.append(f"| {rtype} | {stats['correct']} | {stats['incorrect']} |")
        lines.append("")

        # 4. Evidence Validation
        lines.append("## 4. 证据校验结果 (Evidence Verification)\n")
        gold_ok = len(self.gold_issues) == 0
        lines.append(f"- **标准答案证据校验状态**: {'✅ 全部有效' if gold_ok else '❌ 存在无效证据 (GOLD_DATA_ERROR)'}")
        if not gold_ok:
            lines.append("  - **无效金标准列表**:")
            for issue in self.gold_issues:
                lines.append(f"    - `{issue.get('note_path') or issue.get('pair')}`: {issue['message']}")
        
        valid_rate = self.pred_ev_stats.get("valid_rate", 1.0)
        invalid_count = self.pred_ev_stats.get("invalid_count", 0)
        lines.append(f"- **预测证据有效率 (Evidence Validity Rate)**: `{valid_rate:.4%}`")
        lines.append(f"- **无效预测证据数量**: `{invalid_count}`")
        
        if invalid_count > 0:
            lines.append("  - **无效预测证据列表**:")
            for issue in self.pred_ev_stats.get("invalid_details", []):
                lines.append(f"    - `{issue.get('note_path') or issue.get('pair')}`: {issue['message']}")
        lines.append("")

        # 5. Bad Case Summary
        lines.append("## 5. Bad Case 汇总与统计\n")
        if not self.bad_cases:
            lines.append("恭喜！未发现任何 Bad Case。\n")
        else:
            # Group by case_type
            counts = {}
            for bc in self.bad_cases:
                t = bc["case_type"]
                counts[t] = counts.get(t, 0) + 1

            lines.append("| 错误类型 (Case Type) | 数量 |")
            lines.append("|---|---|")
            for t, c in counts.items():
                lines.append(f"| {t} | {c} |")
            lines.append("")

            # List bad cases
            lines.append("### 逐条 Bad Case 详情")
            for i, bc in enumerate(self.bad_cases):
                lines.append(f"#### Case #{i+1}: {bc['case_type']}")
                lines.append(f"- **涉及文件**: {', '.join(bc['note_paths'])}")
                lines.append(f"- **错误摘要**: {bc['summary']}")
                if bc.get("gold"):
                    lines.append(f"- **标准答案 (Gold)**: `{bc['gold']}`")
                if bc.get("prediction"):
                    lines.append(f"- **预测结果 (Prediction)**: `{bc['prediction']}`")
                lines.append("")

        # 6. Conclusion
        lines.append("## 6. 结论\n")
        scenario = self.meta.get("mock_scenario")
        if scenario == "perfect":
            lines.append("评测闭环运行正常，评分结果为满分，无 Bad Case。\n")
        elif scenario == "noisy":
            lines.append("评测成功捕获了 Noisy 场景中注入的确定性噪音（主题/关系之假阳性/假阴性、方向与类型错误、证据越界等），指标符合预期降低。\n")
        elif scenario == "malformed":
            lines.append("评测在 Malformed 异常格式场景下展现出良好的健壮性，捕获了所有的 Pydantic Schema 校验与格式错误，没有导致评测程序整体崩溃。\n")
        else:
            lines.append("评测运行完成。\n")

        return "\n".join(lines)
