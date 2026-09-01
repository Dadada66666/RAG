from docparser.evaluation.metrics import normalized_edit_similarity, pairwise_order_accuracy


def test_text_metric_is_exact_for_nfc_equivalent_text() -> None:
    assert normalized_edit_similarity("café", "cafe\u0301") == 1.0
    assert normalized_edit_similarity("184,392.17", "184,392.71") < 1.0


def test_pairwise_reading_order_is_independent_metric() -> None:
    assert pairwise_order_accuracy(["a", "b", "c"], [("a", "b"), ("c", "b")]) == 0.5
    assert pairwise_order_accuracy([], []) is None
