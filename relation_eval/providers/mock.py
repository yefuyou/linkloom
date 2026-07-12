from typing import Dict, List, Any
from pydantic import ValidationError
from relation_eval.providers.base import BaseProvider
from relation_eval.schemas import DocumentInput, TopicPrediction, RelationPrediction, RelationEvidence
from relation_eval.dataset.models import GoldDataset

class MockProvider(BaseProvider):
    def __init__(self, gold_dataset: GoldDataset, scenario: str = "perfect"):
        self.gold_dataset = gold_dataset
        self.scenario = scenario.lower()

        # Build note lookup map
        self.note_map = {n.note_path: n for n in gold_dataset.notes}

        # Build pair lookup map (both ordered/unordered key lookup)
        self.pair_map = {}
        for p in gold_dataset.expected_pairs:
            key1 = (p.source, p.target)
            key2 = (p.target, p.source)
            self.pair_map[key1] = p
            self.pair_map[key2] = p

    def predict_topic(self, doc: DocumentInput) -> TopicPrediction:
        path = doc.note_path
        gold = self.note_map.get(path)

        # 1. Malformed scenario triggers
        if self.scenario == "malformed":
            if path == "tests/fixtures/relation_vault/0112-随笔.md":
                # confidence out of bounds
                return TopicPrediction.model_validate({
                    "note_path": path,
                    "predicted_topic_ids": ["agent-evaluation"],
                    "should_link_to_agent_evaluation": True,
                    "confidence": 9.9,
                    "evidence": ["最终答案碰巧蒙对的情况比想象中多"],
                    "reason": "malformed confidence"
                })
            elif path == "tests/fixtures/relation_vault/指标体系梳理.md":
                # evidence is not a list
                return TopicPrediction.model_validate({
                    "note_path": path,
                    "predicted_topic_ids": ["agent-evaluation"],
                    "should_link_to_agent_evaluation": True,
                    "confidence": 0.95,
                    "evidence": "not a list",
                    "reason": "malformed evidence type"
                })

        # 2. Noisy scenario triggers
        if self.scenario == "noisy":
            # Topic FP: 0420-工作日志.md (unrelated in gold) predicted as positive
            if path == "tests/fixtures/relation_vault/0420-工作日志.md":
                return TopicPrediction(
                    note_path=path,
                    predicted_topic_ids=["agent-evaluation"],
                    should_link_to_agent_evaluation=True,
                    confidence=0.85,
                    evidence=["核心页面的回归覆盖率到 80% 了"],
                    reason="noisy topic FP"
                )
            # Topic FN: 灵感-关于状态丢失.md (positive in gold) predicted as negative
            elif path == "tests/fixtures/relation_vault/灵感-关于状态丢失.md":
                return TopicPrediction(
                    note_path=path,
                    predicted_topic_ids=[],
                    should_link_to_agent_evaluation=False,
                    confidence=0.90,
                    evidence=[],
                    reason="noisy topic FN"
                )
            # Evidence not in source error: 指标体系梳理.md
            elif path == "tests/fixtures/relation_vault/指标体系梳理.md":
                return TopicPrediction(
                    note_path=path,
                    predicted_topic_ids=["agent-evaluation"],
                    should_link_to_agent_evaluation=True,
                    confidence=0.95,
                    evidence=["this text is definitely not in the note content"],
                    reason="noisy topic evidence not in source"
                )

        # 3. Perfect (default fallback)
        if not gold:
            # Fallback for unrecognized document
            return TopicPrediction(
                note_path=path,
                predicted_topic_ids=[],
                should_link_to_agent_evaluation=False,
                confidence=1.0,
                evidence=[],
                reason="not in gold"
            )

        # Perfect mapping
        return TopicPrediction(
            note_path=path,
            predicted_topic_ids=["agent-evaluation"] if gold.should_link_to_agent_evaluation else [],
            should_link_to_agent_evaluation=gold.should_link_to_agent_evaluation,
            confidence=1.0,
            evidence=gold.evidence if gold.should_link_to_agent_evaluation else [],
            reason=f"perfect mock: {gold.rationale}"
        )

    def predict_relation(self, left: DocumentInput, right: DocumentInput) -> RelationPrediction:
        path_a = left.note_path
        path_b = right.note_path
        gold = self.pair_map.get((path_a, path_b))

        # 1. Malformed scenario triggers
        if self.scenario == "malformed":
            if (path_a == "tests/fixtures/relation_vault/Q1阶段性复盘.md" and 
                path_b == "tests/fixtures/relation_vault/下半年部门会议.md") or \
               (path_b == "tests/fixtures/relation_vault/Q1阶段性复盘.md" and 
                path_a == "tests/fixtures/relation_vault/下半年部门会议.md"):
                # should_link=True but relation_type is null
                return RelationPrediction.model_validate({
                    "left_note_path": path_a,
                    "right_note_path": path_b,
                    "should_link": True,
                    "source": path_a,
                    "target": path_b,
                    "relation_type": None,
                    "confidence": 0.9,
                    "evidence": {"source": [], "target": []},
                    "reason": "malformed fields"
                })

        # 2. Noisy scenario triggers
        if self.scenario == "noisy":
            # Relation FP: 本周流水账-0418.md & 0420-工作日志.md (unrelated in gold) predicted as linked
            if ((path_a == "tests/fixtures/relation_vault/本周流水账-0418.md" and path_b == "tests/fixtures/relation_vault/0420-工作日志.md") or
                (path_b == "tests/fixtures/relation_vault/本周流水账-0418.md" and path_a == "tests/fixtures/relation_vault/0420-工作日志.md")):
                return RelationPrediction(
                    left_note_path=path_a,
                    right_note_path=path_b,
                    should_link=True,
                    source="tests/fixtures/relation_vault/本周流水账-0418.md",
                    target="tests/fixtures/relation_vault/0420-工作日志.md",
                    relation_type="concept-to-practice",
                    confidence=0.88,
                    evidence=RelationEvidence(
                        source=["CI 节点磁盘又满了"],
                        target=["支付台重构验收"]
                    ),
                    reason="noisy relation FP"
                )
            # Relation FN: 0112-随笔.md & Q1阶段性复盘.md (linked in gold) predicted as unlinked
            elif ((path_a == "tests/fixtures/relation_vault/0112-随笔.md" and path_b == "tests/fixtures/relation_vault/Q1阶段性复盘.md") or
                  (path_b == "tests/fixtures/relation_vault/0112-随笔.md" and path_a == "tests/fixtures/relation_vault/Q1阶段性复盘.md")):
                return RelationPrediction(
                    left_note_path=path_a,
                    right_note_path=path_b,
                    should_link=False,
                    source=None,
                    target=None,
                    relation_type=None,
                    confidence=0.95,
                    evidence=RelationEvidence(source=[], target=[]),
                    reason="noisy relation FN"
                )
            # Relation Type Error: 指标体系梳理.md & 面经-第二轮.md (concept-to-interview -> concept-to-practice)
            elif ((path_a == "tests/fixtures/relation_vault/指标体系梳理.md" and path_b == "tests/fixtures/relation_vault/面经-第二轮.md") or
                  (path_b == "tests/fixtures/relation_vault/指标体系梳理.md" and path_a == "tests/fixtures/relation_vault/面经-第二轮.md")):
                return RelationPrediction(
                    left_note_path=path_a,
                    right_note_path=path_b,
                    should_link=True,
                    source="tests/fixtures/relation_vault/指标体系梳理.md",
                    target="tests/fixtures/relation_vault/面经-第二轮.md",
                    relation_type="concept-to-practice",
                    confidence=0.90,
                    evidence=RelationEvidence(
                        source=["一条 case 多个错误怎么归因"],
                        target=["检索没找到正确段落就是检索的问题，找到了但抽错就是抽取参数的问题，抽对了但定位校验没拦住就是校验规则的问题"]
                    ),
                    reason="noisy relation type error"
                )
            # Relation Direction Error: 本周流水账-0418.md & 周二沟通记录.md (swapped source/target)
            elif ((path_a == "tests/fixtures/relation_vault/本周流水账-0418.md" and path_b == "tests/fixtures/relation_vault/周二沟通记录.md") or
                  (path_b == "tests/fixtures/relation_vault/本周流水账-0418.md" and path_a == "tests/fixtures/relation_vault/周二沟通记录.md")):
                return RelationPrediction(
                    left_note_path=path_a,
                    right_note_path=path_b,
                    should_link=True,
                    source="tests/fixtures/relation_vault/周二沟通记录.md",
                    target="tests/fixtures/relation_vault/本周流水账-0418.md",
                    relation_type="practice-to-interview",
                    confidence=0.92,
                    evidence=RelationEvidence(
                        source=["怎么在上线前就能验证每条路径还是对的"],
                        target=["抽取中间每一跳的节点序列，跟预设的标准序列做 diff"]
                    ),
                    reason="noisy relation direction error"
                )

        # 3. Perfect fallback
        if not gold or not gold.should_link:
            return RelationPrediction(
                left_note_path=path_a,
                right_note_path=path_b,
                should_link=False,
                source=None,
                target=None,
                relation_type=None,
                confidence=1.0,
                evidence=RelationEvidence(source=[], target=[]),
                reason="perfect mock: unrelated"
            )

        # Perfect linked pair
        return RelationPrediction(
            left_note_path=path_a,
            right_note_path=path_b,
            should_link=True,
            source=gold.source,
            target=gold.target,
            relation_type=gold.relation_type,
            confidence=1.0,
            evidence=RelationEvidence(
                source=gold.evidence.source,
                target=gold.evidence.target
            ),
            reason=f"perfect mock: {gold.rationale}"
        )
