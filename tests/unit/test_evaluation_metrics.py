from linkloom.evaluation.metrics import binary_metrics, relation_metrics, evidence_metrics, retrieval_hit_at_k, retrieval_mrr

def test_binary_metrics_zero_denom():
    res = binary_metrics(0, 0, 0, 0)
    assert res["accuracy"] == 0.0
    assert res["precision"] == 0.0

def test_binary_metrics_normal():
    res = binary_metrics(tp=8, fp=2, tn=8, fn=2)
    assert res["accuracy"] == 0.8
    assert res["precision"] == 0.8
    assert res["recall"] == 0.8

def test_relation_metrics():
    res = relation_metrics(link_tp=5, link_fp=5, link_fn=0, direction_correct=4, direction_total=5, type_correct=3, type_total=5)
    assert res["link_precision"] == 0.5
    assert res["link_recall"] == 1.0
    assert res["link_f1"] == 0.6667
    assert res["direction_accuracy"] == 0.8
