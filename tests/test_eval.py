"""No-network checks on eval scoring logic. Run: python tests/test_eval.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "eval"))
from eval import build_context, keyword_hit, retrieval_hit  # noqa: E402


def test_retrieval_hit():
    assert retrieval_hit({"expected_title": "Naruto"}, {"Naruto", "Bleach"})
    assert not retrieval_hit({"expected_title": "Naruto"}, {"Bleach"})
    assert retrieval_hit({"expected_titles_any": ["Naruto", "One Piece"]}, {"One Piece"})
    assert not retrieval_hit({"expected_titles_any": ["Naruto"]}, set())
    assert not retrieval_hit({}, {"Naruto"})


def test_keyword_hit():
    assert keyword_hit({"expected_keywords": ["Titan"]}, "Humans fight the TITANS.")
    assert not keyword_hit({"expected_keywords": ["Titan"]}, "No giants here.")
    assert not keyword_hit({}, "anything")
    # regression: model emitted U+202F narrow no-break space inside "Pirate King"
    assert keyword_hit({"expected_keywords": ["Pirate King"]}, "the **Pirate\u202fKing** title")
    # regression: model emitted U+2011 non-breaking hyphen inside "K-ON!"
    assert keyword_hit({"expected_keywords": ["K-ON"]}, "shows like K\u2011ON! are popular")


def test_build_context():
    filter_rows = [{"title": "Naruto", "metadata": {"genres": ["Action"], "episodes": 220, "format": "TV"}, "total_count": 1}]
    ctx = build_context("filter_lookup", filter_rows)
    assert ctx == (
        'Total matching entries in the database: 1. Showing 1 below '
        '(use the total above for any "how many" question, not a count of the list shown).\n'
        "[Naruto] genres: Action, episodes: 220, format: TV"
    )

    chunks = [{"title": "Naruto", "chunk_text": "A ninja story."}]
    assert build_context("semantic_search", chunks) == "[Naruto] A ninja story."


def test_qa_pairs_schema():
    pairs = json.loads((Path(__file__).parent.parent / "eval" / "qa_pairs.json").read_text())
    filled = [p for p in pairs if p.get("question")]
    assert len(filled) >= 20
    for p in filled:
        assert p.get("expected_title") or p.get("expected_titles_any"), p["question"]
        assert p.get("expected_keywords"), p["question"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
