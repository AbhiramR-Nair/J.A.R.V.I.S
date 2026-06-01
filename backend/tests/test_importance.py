"""Smoke tests for the heuristic importance scorer.

Tests only _heuristic_score() — no network calls, no LLM, runs instantly.

Run with:
    python -m pytest backend/tests/test_importance.py -v
Or directly (no pytest needed):
    python backend/tests/test_importance.py
"""

from backend.memory.importance import _heuristic_score

THRESHOLD = 4.0  # mirrors settings.importance_threshold default


def test_trivial_greeting_is_filtered():
    text = "User: hi\nAssistant: Hello! How can I help you today?"
    assert _heuristic_score(text) < THRESHOLD, f"Expected < {THRESHOLD}, got {_heuristic_score(text)}"


def test_ok_is_filtered():
    text = "User: ok\nAssistant: Got it."
    assert _heuristic_score(text) < THRESHOLD, f"Expected < {THRESHOLD}, got {_heuristic_score(text)}"


def test_thanks_is_filtered():
    text = "User: thanks\nAssistant: You're welcome!"
    assert _heuristic_score(text) < THRESHOLD, f"Expected < {THRESHOLD}, got {_heuristic_score(text)}"


def test_explicit_log_this_is_max():
    text = "User: log this: T315I shows 40-fold resistance shift\nAssistant: Logged."
    score = _heuristic_score(text)
    assert score >= 9, f"Expected >= 9, got {score}"


def test_remember_signal_is_max():
    text = "User: remember that we're using the 40-fold threshold for significance\nAssistant: Understood."
    score = _heuristic_score(text)
    assert score >= 9, f"Expected >= 9, got {score}"


def test_mutation_identifier_scores_high():
    text = "User: what does T315I do?\nAssistant: T315I is the gatekeeper mutation in ABL1 that confers broad resistance to TKIs."
    score = _heuristic_score(text)
    assert score >= THRESHOLD, f"Expected >= {THRESHOLD}, got {score}"


def test_gene_name_scores_high():
    text = "User: is ABL1 overexpressed here?\nAssistant: Yes, BCR-ABL1 fusion drives constitutive kinase activity."
    score = _heuristic_score(text)
    assert score >= THRESHOLD, f"Expected >= {THRESHOLD}, got {score}"


def test_unit_scores_high():
    text = "User: what dose was used?\nAssistant: The IC50 was measured at 2.5 μM under standard conditions."
    score = _heuristic_score(text)
    assert score >= THRESHOLD, f"Expected >= {THRESHOLD}, got {score}"


def test_long_substantive_text_scores_above_threshold():
    text = (
        "User: explain the difference between imatinib and nilotinib\n"
        "Assistant: Imatinib and nilotinib are both BCR-ABL tyrosine kinase inhibitors. "
        "Nilotinib is more selective and has higher binding affinity. It is effective against "
        "most imatinib-resistant mutations. The key structural difference is that nilotinib "
        "lacks the N-methylpiperazine group and has additional hydrophobic interactions."
    )
    score = _heuristic_score(text)
    assert score >= THRESHOLD, f"Expected >= {THRESHOLD}, got {score}"


if __name__ == "__main__":
    tests = [
        test_trivial_greeting_is_filtered,
        test_ok_is_filtered,
        test_thanks_is_filtered,
        test_explicit_log_this_is_max,
        test_remember_signal_is_max,
        test_mutation_identifier_scores_high,
        test_gene_name_scores_high,
        test_unit_scores_high,
        test_long_substantive_text_scores_above_threshold,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
