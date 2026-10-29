"""The answer evaluation's metrics, computed from hand-written answers and verdicts."""

import json

from investigator.evaluation import answers
from investigator.evaluation.answers import AnswerQuestion, compare, score_config

QUESTIONS = [
    AnswerQuestion("Q1", "synthetic", "multi_doc", "q1", "ref", ["upload:a", "upload:b"]),
    AnswerQuestion("Q2", "synthetic", "contradiction", "q2", "ref", ["upload:a"],
                   expect_contradiction=True),
    AnswerQuestion("Q3", "synthetic", "unanswerable", "q3", "not documented", [],
                   expect_abstain=True),
]  # fmt: skip


def answer(qid, cited, seen, status="answered", contradictions=(), verification=()):
    return {
        "id": qid, "status": status, "citations": [{"source_id": s} for s in cited],
        "evidence_sources": seen, "contradictions": list(contradictions),
        "verification": list(verification), "searches": ["x"], "llm_calls": 3,
        "prompt_tokens": 900, "completion_tokens": 100, "duration_ms": 2000,
    }  # fmt: skip


def write(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_score_config_computes_every_metric(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "ANSWERS_DIR", tmp_path)
    supported = {"verdict": "supported"}
    write(tmp_path / "cfg.jsonl", [
        # cites a (gold) and c (not gold); saw a and b; 2 of 3 claims supported
        answer("Q1", ["upload:a", "upload:c"], ["upload:a", "upload:b", "upload:c"],
               verification=[supported, supported, {"verdict": "unsupported"}]),
        answer("Q2", ["upload:a"], ["upload:a"],
               contradictions=[{"topic": "t", "positions": []}]),
        answer("Q3", [], [], status="insufficient_evidence"),
    ])  # fmt: skip
    write(
        tmp_path / "cfg.judgments.jsonl",
        [
            {"id": "Q1", "verdict": "partial", "reason": "r"},
            {"id": "Q2", "verdict": "correct", "reason": "r"},
            {"id": "Q3", "verdict": "correct", "reason": "r"},
        ],
    )

    summary, rows = score_config("cfg", QUESTIONS)

    assert summary["score"] == round((0.5 + 1 + 1) / 3, 3)
    assert (summary["correct"], summary["partial"], summary["incorrect"]) == (2, 1, 0)
    assert summary["citation_precision"] == round((0.5 + 1.0) / 2, 3)  # Q3 cites nothing
    assert summary["citation_recall"] == round((0.5 + 1.0) / 2, 3)  # Q3 has no gold docs
    assert summary["evidence_recall"] == 1.0
    assert summary["claim_support_rate"] == round(2 / 3, 3)
    assert summary["contradictions_detected"] == "1/1"
    assert summary["abstention_correct"] == "1/1"
    assert summary["score_by_group"]["synthetic/multi_doc"] == 0.5


def test_unjudged_answers_are_left_out_not_counted_as_wrong(tmp_path, monkeypatch):
    monkeypatch.setattr(answers, "ANSWERS_DIR", tmp_path)
    write(tmp_path / "cfg.jsonl", [answer("Q1", [], []), answer("Q2", [], [])])
    write(tmp_path / "cfg.judgments.jsonl", [{"id": "Q1", "verdict": "correct"}])

    summary, _ = score_config("cfg", QUESTIONS)

    assert summary["questions"] == 1 and summary["score"] == 1.0


def test_comparisons_leave_out_runs_that_failed_on_either_side():
    def row(qid, score, status="answered"):
        return {"id": qid, "score": score, "status": status}

    # Q3 failed for "a" (e.g. quota spent): an outage, not a wrong answer, so it is not compared
    a = [row("Q1", 0.5), row("Q2", 1.0), row("Q3", 0.0, status="failed")]
    b = [row("Q1", 1.0), row("Q2", 1.0), row("Q3", 1.0)]

    result = compare("a", a, "b", b)

    assert result["questions"] == 2
    assert (result["from_score"], result["to_score"]) == (0.75, 1.0)
    assert result["difference"] == 0.25
