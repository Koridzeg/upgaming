"""Text -> 384-dimensional vectors with BAAI/bge-small-en-v1.5, run directly on ONNX Runtime.

What an embedding model does, step by step (all visible in `_embed_batch`):
1. tokenize into WordPiece ids (with [CLS] first and [SEP] last), pad the batch to equal length;
2. the transformer returns one 384-d vector per token (`last_hidden_state`);
3. pooling: bge was trained to put the meaning of the whole text into the [CLS] token's vector
   (its 1_Pooling/config.json: pooling_mode_cls_token = true), so that vector is the embedding;
4. L2-normalise, so cosine similarity is a plain dot product.

Queries get an instruction prefix, documents don't: bge was trained with this asymmetry for
retrieval (short question -> longer passage).

The model runs int8-quantized: measured on 1,000 HotpotQA paragraphs it is 1.7x faster than
fp32 (58 vs 34 texts/s), 4x smaller (34 vs 133 MB), its vectors agree with fp32 at cosine 0.98,
and it ranked the right paragraph first as often (98.0% vs 97.7% on 300 self-retrieval queries).

Usage (create the quantized model file ahead of time, e.g. in a Docker build):
    python -m investigator.embedding
"""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import asyncpg
import numpy as np

from investigator.config import DATA_DIR
from investigator.ingestion.tokenizer import TOKENIZER_MODEL, TOKENIZER_REVISION

DIMENSIONS = 384
MAX_TOKENS = 512
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
MODEL_PATH = DATA_DIR / "models" / f"bge-small-en-v1.5-{TOKENIZER_REVISION[:7]}-int8.onnx"


class Embedder(Protocol):
    name: str
    sha256: str  # identifies the exact model weights

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class EmbeddingModelMismatch(RuntimeError):
    pass


def prepare_model(path: Path = MODEL_PATH) -> Path:
    """Download the pinned fp32 ONNX model and write its int8 version, once."""
    if path.exists():
        return path
    from huggingface_hub import hf_hub_download
    from onnxruntime.quantization import QuantType, quantize_dynamic  # needs the `onnx` package

    fp32 = hf_hub_download(TOKENIZER_MODEL, "onnx/model.onnx", revision=TOKENIZER_REVISION)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial.onnx")
    # Dynamic quantization: weights stored as int8, activations quantized on the fly per batch.
    quantize_dynamic(fp32, partial, weight_type=QuantType.QInt8)
    partial.replace(path)
    return path


class OnnxEmbedder:
    def __init__(self, path: Path = MODEL_PATH, batch_size: int = 64, threads: int = 0):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        path = prepare_model(path)
        self.name = f"{TOKENIZER_MODEL}@{TOKENIZER_REVISION[:7]}-int8"
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.batch_size = batch_size

        self._tokenizer = Tokenizer.from_pretrained(TOKENIZER_MODEL, revision=TOKENIZER_REVISION)
        self._tokenizer.enable_truncation(MAX_TOKENS)  # the model has 512 position embeddings
        self._tokenizer.enable_padding()  # pad each batch to its longest text

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads  # 0 = ONNX Runtime picks (all physical cores)
        self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """(len(texts), 384) float32, unit length. Order matches the input."""
        if not texts:
            return np.zeros((0, DIMENSIONS), dtype=np.float32)
        # Batch texts of similar length together: padding then wastes almost nothing, and the
        # transformer's cost grows with the padded length of the batch.
        order = np.argsort([len(t) for t in texts], kind="stable")
        vectors = np.empty((len(texts), DIMENSIONS), dtype=np.float32)
        for start in range(0, len(texts), self.batch_size):
            batch = order[start : start + self.batch_size]
            vectors[batch] = self._embed_batch([texts[i] for i in batch])
        return vectors

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed_batch([QUERY_INSTRUCTION + text])[0]

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        (hidden,) = self._session.run(
            ["last_hidden_state"],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,  # 0 marks padding the model must ignore
                "token_type_ids": np.zeros_like(input_ids),  # single-segment input
            },
        )
        cls = hidden[:, 0, :]  # (batch, 384): the [CLS] token's vector
        return cls / np.linalg.norm(cls, axis=1, keepdims=True)


def embedding_input(context: str, text: str) -> str:
    """What gets embedded for a chunk: its context header, then its text."""
    return f"{context}\n\n{text}" if context else text


async def ensure_embedding_model(conn: asyncpg.Connection, embedder: Embedder) -> None:
    """Record the model on first use; refuse vectors from any other model afterwards."""
    await conn.execute(
        """
        INSERT INTO embedding_model (name, sha256, dimensions) VALUES ($1, $2, $3)
        ON CONFLICT (singleton) DO NOTHING
        """,
        embedder.name,
        embedder.sha256,
        DIMENSIONS,
    )
    stored = await conn.fetchrow("SELECT name, sha256 FROM embedding_model")
    if stored["sha256"] != embedder.sha256:
        raise EmbeddingModelMismatch(
            f"The index holds vectors from {stored['name']} ({stored['sha256'][:12]}), "
            f"not {embedder.name} ({embedder.sha256[:12]}). Re-embed everything or use that model."
        )


@lru_cache
def get_embedder() -> OnnxEmbedder:
    return OnnxEmbedder()


if __name__ == "__main__":
    print(prepare_model())
