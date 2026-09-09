from scripts.evaluate_retrieval_eval_v1 import score_ranking


def _question():
    return {
        "relevant_targets": [
            {
                "document_id": "right_model",
                "section": "1 产品概览",
                "evidence_fragments": ["最大功率为 45 W"],
            }
        ],
        "hard_negative_document_ids": ["wrong_model_pro"],
    }


def test_score_ranking_requires_document_section_and_evidence():
    results = [
        {
            "document_id": "wrong_model_pro",
            "section": "产品概览",
            "section_path": "1 产品概览",
            "content": "最大功率为 80 W",
        },
        {
            "document_id": "right_model",
            "section": "产品概览",
            "section_path": "1 产品概览",
            "content": "最大功率为 45 W",
        },
    ]

    metrics, detail = score_ranking(results, _question(), top_k=5)

    assert metrics["document_hit@1"] == 0.0
    assert metrics["section_hit@3"] == 1.0
    assert metrics["evidence_recall@3"] == 1.0
    assert metrics["context_precision@5"] == 0.2
    assert metrics["answerable@5"] == 1.0
    assert metrics["mrr"] == 0.5
    assert metrics["hard_negative_outrank_rate"] == 1.0
    assert detail["hard_negative_outranks"] is True


def test_score_ranking_does_not_accept_evidence_from_wrong_document():
    results = [
        {
            "document_id": "wrong_model_pro",
            "section": "产品概览",
            "section_path": "1 产品概览",
            "content": "最大功率为 45 W",
        }
    ]

    metrics, detail = score_ranking(results, _question(), top_k=5)

    assert metrics["document_hit@5"] == 0.0
    assert metrics["section_hit@5"] == 0.0
    assert metrics["evidence_recall@5"] == 0.0
    assert metrics["context_precision@5"] == 0.0
    assert metrics["answerable@5"] == 0.0
    assert detail["missing_evidence"] == ["最大功率为 45 W"]


def test_context_precision_counts_only_evidence_bearing_target_chunks():
    results = [
        {
            "document_id": "right_model",
            "section_path": "1 产品概览",
            "content": "最大功率为 45 W",
        },
        {
            "document_id": "right_model",
            "section_path": "1 产品概览",
            "content": "这是同一章节的介绍，但不包含标准证据。",
        },
        {
            "document_id": "wrong_model_pro",
            "section_path": "1 产品概览",
            "content": "最大功率为 45 W",
        },
        {
            "document_id": "right_model",
            "section_path": "2 安全要求",
            "content": "最大功率为 45 W",
        },
        {
            "document_id": "other_model",
            "section_path": "3 供电规格",
            "content": "无关内容",
        },
    ]

    metrics, detail = score_ranking(results, _question(), top_k=5)

    assert metrics["context_precision@5"] == 0.2
    assert [item["evidence_match"] for item in detail["results"]] == [
        True,
        False,
        False,
        False,
        False,
    ]
