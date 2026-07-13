"""Vector helpers: float32 (de)serialization, brute-force cosine top-k, RRF merge.

ponytail: brute-force cosine over in-memory vectors — trivial for a single-user
bot (<~10k rows). Swap for sqlite-vec/ANN only if fact count blows past that.
"""

import numpy as np


def to_blob(vec) -> bytes:
    """np array / list → compact float32 bytes for SQLite BLOB storage."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def cosine_topk(qvec, rows: list, k: int = 20) -> list:
    """rows: tuples whose LAST element is an embedding BLOB (or None).
    Returns top-k rows (embedding stripped) by cosine similarity to qvec."""
    mats, payloads = [], []
    for r in rows:
        blob = r[-1]
        if not blob:
            continue
        mats.append(from_blob(blob))
        payloads.append(r[:-1])
    if not mats:
        return []
    M = np.vstack(mats)
    M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    q = _unit(np.asarray(qvec, dtype=np.float32))
    sims = M @ q
    return [payloads[i] for i in np.argsort(-sims)[:k]]


def rrf(*lists, k: int = 60, key=lambda x: x) -> list:
    """Reciprocal Rank Fusion — merge ranked lists, dedup by key(), rank by
    fused score. Scale-free: needs only per-list rank, not comparable scores."""
    scores, seen = {}, {}
    for lst in lists:
        for rank, item in enumerate(lst):
            kk = key(item)
            scores[kk] = scores.get(kk, 0.0) + 1.0 / (k + rank + 1)
            seen.setdefault(kk, item)
    return [seen[kk] for kk in sorted(scores, key=lambda x: -scores[x])]


def _demo():
    """Self-check: python -m app.lib.vectors — no network."""
    # serde round-trip
    v = [0.1, 0.2, 0.3]
    assert np.allclose(from_blob(to_blob(v)), v, atol=1e-6), "serde round-trip"

    # cosine_topk: query nearest to row 'a'
    q = [1.0, 0.0]
    rows = [
        ("a", to_blob([1.0, 0.05])),
        ("b", to_blob([0.0, 1.0])),
        ("c", to_blob([-1.0, 0.0])),
    ]
    top = cosine_topk(q, rows, k=2)
    assert top[0] == ("a",), f"nearest should be a, got {top}"
    assert ("c",) not in top, "opposite vector should not be top-2"

    # rows with no embedding are skipped
    assert cosine_topk(q, [("x", None)]) == [], "null embedding skipped"

    # rrf: item ranked top in BOTH lists wins outright
    l1 = ["y", "x", "z"]
    l2 = ["y", "w", "x"]
    fused = rrf(l1, l2)
    assert fused[0] == "y", f"y ranks top in both, got {fused}"
    assert set(fused) == {"x", "y", "z", "w"}, "rrf must union + dedup"

    print("vectors self-check OK")


if __name__ == "__main__":
    _demo()
