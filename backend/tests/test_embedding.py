"""The real int8 model (downloaded and quantized on first use, then cached)."""

import numpy as np

from investigator.embedding import DIMENSIONS, get_embedder

DOCUMENTS = [
    "The fraud model v3.3 rollout raised fraud-scoring p99 latency to 450 ms.",
    "Kafka brokers were upgraded from 3.6 to 3.7 on 23 March.",
    "Arthur's Magazine was an American literary periodical published in Philadelphia.",
]


def test_vectors_have_the_right_shape_and_unit_length():
    vectors = get_embedder().embed_documents(DOCUMENTS)

    assert vectors.shape == (3, DIMENSIONS)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_query_is_closest_to_the_relevant_document():
    embedder = get_embedder()
    scores = embedder.embed_documents(DOCUMENTS) @ embedder.embed_query(
        "What made fraud scoring slow?"
    )

    assert np.argmax(scores) == 0
    assert scores[0] > scores[1] > scores[2]


def test_batching_keeps_input_order_and_barely_changes_vectors():
    embedder = get_embedder()
    together = embedder.embed_documents(DOCUMENTS)
    one_by_one = np.vstack([embedder.embed_documents([d]) for d in DOCUMENTS])

    # Each row still belongs to its own text despite the length sorting. The vectors are not
    # bit-identical: int8 dynamic quantization scales activations per batch, so batch-mates
    # shift a vector slightly (measured: mean cosine 0.9966 on 200 HotpotQA paragraphs).
    assert np.all(np.sum(together * one_by_one, axis=1) > 0.99)


def test_model_identity_is_stable():
    embedder = get_embedder()

    assert embedder.name.endswith("-int8")
    assert len(embedder.sha256) == 64
