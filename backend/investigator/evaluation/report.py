"""Markdown rendering of evaluation results."""


def markdown_table(headers: list[str], rows: list[list]) -> list[str]:
    def cell(value) -> str:
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value).replace("|", "/")

    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows),
    ]


RETRIEVAL_METRICS = [
    "recall@5", "recall@10", "all@10", "all@20", "mrr", "ndcg@10", "sentence_recall@10",
]  # fmt: skip


def retrieval_report(results: dict, primary: str) -> str:
    sample = results["sample"]
    lines = [
        "# Retrieval evaluation",
        "",
        f"HotpotQA: {sample['tune']} tune / {sample['test']} test questions, seeded and "
        f"stratified by type. Synthetic corpus: {sample['synthetic']} answerable questions.",
        f"Selected on the tune split by {primary}: **{results['tuned']['config']}**.",
        "",
        "## Test split",
        "",
    ]
    lines += markdown_table(
        ["Configuration", *RETRIEVAL_METRICS, "tokens@10"],
        [
            [name, *(s.get(m, 0.0) for m in RETRIEVAL_METRICS), round(s["tokens@10"])]
            for name, s in results["test"].items()
        ],
    )

    lines += ["", "## Paired bootstrap, 95% confidence intervals", ""]
    lines += markdown_table(
        ["From", "To", "Metric", "Difference", "95% CI"],
        [
            [c["from"], c["to"], c["metric"], f"{c['difference']:+.3f}",
             f"[{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]"]
            for c in results["comparisons"]
        ],
    )  # fmt: skip

    f = results["failures"]
    lines += [
        "",
        "## Failure analysis (tuned setting, structural chunks, test split)",
        "",
        f"- {primary} by question type: {f['all@10_by_group']} (questions: {f['group_sizes']})",
        f"- Gold documents missing from the top 10: {f['missed_gold_documents']}",
        f"- ... never retrieved by either retriever (not in the top 50): "
        f"{f['share_never_retrieved']:.0%}",
        f"- ... whose title the question names: {f['share_named_in_question']:.0%}",
        "",
    ]
    lines += markdown_table(
        ["Question", "Type", "Missed document", "Named in question", "In candidates"],
        [
            [e["question"], e["group"], e["missed"], e["named_in_question"], e["in_candidates"]]
            for e in f["examples"]
        ],
    )

    lines += ["", "## Synthetic corpus (structural chunks)", ""]
    lines += markdown_table(
        ["Configuration", "recall@10", "all@10", "mrr"],
        [[name, s["recall@10"], s["all@10"], s["mrr"]] for name, s in results["synthetic"].items()],
    )
    return "\n".join(lines) + "\n"
