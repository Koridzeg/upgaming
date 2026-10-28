from investigator.evaluation.datasets import _draw_sample


def records():
    bridge = [{"id": f"b{i:03d}", "type": "bridge", "labels_reliable": True} for i in range(80)]
    comparison = [
        {"id": f"c{i:03d}", "type": "comparison", "labels_reliable": True} for i in range(20)
    ]
    unreliable = [{"id": "x000", "type": "bridge", "labels_reliable": False}]
    return bridge + comparison + unreliable


def test_sample_is_stratified_disjoint_and_reproducible():
    sample = _draw_sample(records(), per_split=10)

    for split in ("tune", "test"):
        ids = sample[split]
        assert sum(i.startswith("b") for i in ids) == 8  # 80% bridge, as in the data
        assert sum(i.startswith("c") for i in ids) == 2
    assert not set(sample["tune"]) & set(sample["test"])
    assert "x000" not in sample["tune"] + sample["test"]  # unreliable labels never sampled
    assert _draw_sample(list(reversed(records())), per_split=10) == sample  # order-independent
