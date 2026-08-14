"""Offline sparse-tokenizer tests. The FIXTURE dict is duplicated verbatim in
tests/test_sparse.mjs -- both suites assert the same hashes so the Python
(ingest-time) and JS (query-time) implementations can't drift silently.
"""
import sys

sys.path.insert(0, "ingest")
from qdrant_store import sparse_vector, tokenize

# text -> (indices, values). Regenerate BOTH files together if tokenizer changes.
FIXTURE = {
    "one piece one": ([3123124719, 4106613381], [2.0, 1.0]),
    "One Piece: Pirate King ワンピース K‑ON!": (
        [287202141, 435066257, 3026372832, 3123124719, 3658736598, 4106613381],
        [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    ),
    "a I x": ([], []),  # single-char tokens dropped
    "": ([], []),
}


def main():
    assert tokenize("One Piece: Pirate King") == ["one", "piece", "pirate", "king"]
    assert tokenize("K‑ON!") == ["k‐on"]  # U+2011 NFKC-normalizes to U+2010, kept in token
    for text, (indices, values) in FIXTURE.items():
        sv = sparse_vector(text)
        assert sv.indices == indices, f"{text!r}: {sv.indices} != {indices}"
        assert sv.values == values, f"{text!r}: {sv.values} != {values}"
    print("test_sparse.py: all assertions passed")


if __name__ == "__main__":
    main()
