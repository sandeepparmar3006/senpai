"""Fetch MAL reviews for already-ingested anime and load them as a second source (opinion_search), in resumable batches."""
import argparse
import json
from pathlib import Path

from fetch_jikan_reviews import fetch_all
from chunk_and_embed_reviews import process
from load_to_supabase import load

DATA_DIR = Path(__file__).parent.parent / "data"
PROGRESS_FILE = DATA_DIR / "reviews_ingest_progress.json"
RAW_REVIEWS_FILE = DATA_DIR / "raw_reviews.json"


def load_progress() -> int:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())["next_index"]
    return 0


def save_progress(index: int) -> None:
    PROGRESS_FILE.write_text(json.dumps({"next_index": index}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=60)
    parser.add_argument("--restart", action="store_true", help="ignore saved progress, start from entry 0")
    args = parser.parse_args()

    anilist = json.loads((DATA_DIR / "raw_anilist.json").read_text())
    start = 0 if args.restart else load_progress()
    all_reviews = json.loads(RAW_REVIEWS_FILE.read_text()) if RAW_REVIEWS_FILE.exists() and not args.restart else []

    if start >= len(anilist):
        print("Already complete.")
        raise SystemExit

    for i in range(start, len(anilist), args.batch_size):
        batch = anilist[i : i + args.batch_size]
        print(f"Batch {i}-{i + len(batch)} of {len(anilist)}...")

        reviews, skipped, aborted_early = fetch_all(batch)
        all_reviews.extend(reviews)
        RAW_REVIEWS_FILE.write_text(json.dumps(all_reviews, indent=2))
        print(f"  {len(reviews)} reviews fetched this batch ({len(all_reviews)} total)")

        chunks = process(reviews)
        print(f"  {len(chunks)} chunks embedded")

        count = load(chunks, source="jikan_review")
        print(f"  {count} rows loaded")

        # a batch that got cut short (aborted_early) or mostly failed looks like
        # Jikan being blocked, not transient flakiness — don't mark it "done" or a
        # future resume would silently skip retrying it
        if aborted_early or skipped / len(batch) > 0.4:
            print(f"  batch skip rate too high ({skipped}/{len(batch)}) — Jikan looks blocked, stopping without advancing checkpoint")
            print(f"  resume later with the same command once Jikan recovers; it will retry from index {i}")
            break

        save_progress(i + len(batch))

    print("Done.")
