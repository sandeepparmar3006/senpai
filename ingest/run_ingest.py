"""Orchestrate the full ingestion pipeline: fetch -> embed -> load, in resumable batches.

Embeds and loads each batch of entries into Qdrant immediately, rather than
accumulating the whole corpus in memory and loading once at the end -- a
crash only costs the in-progress batch, not the entire run.
"""
import argparse
import json
from pathlib import Path

from fetch_anilist import fetch_all
from chunk_and_embed import process
from load_to_qdrant import load

DATA_DIR = Path(__file__).parent.parent / "data"
PROGRESS_FILE = DATA_DIR / "ingest_progress.json"
RAW_FILE = DATA_DIR / "raw_anilist.json"
CACHE_PATH = DATA_DIR / "embedded.json"


def load_progress() -> int:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())["next_index"]
    return 0


def save_progress(index: int) -> None:
    PROGRESS_FILE.write_text(json.dumps({"next_index": index}))


def load_cache() -> dict:
    cache = {}
    if CACHE_PATH.exists():
        try:
            cached_data = json.loads(CACHE_PATH.read_text())
            cache = {item["source_id"]: {"chunk_text": item.get("chunk_text"), "embedding": item["embedding"]} for item in cached_data}
            print(f"  Loaded {len(cache)} cached embeddings")
        except Exception as e:
            print(f"  Failed to load cache: {e}")
    return cache


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=5, help="AniList pages (50 entries each)")
    parser.add_argument("--batch-size", type=int, default=200, help="entries embedded + loaded per batch")
    parser.add_argument("--restart", action="store_true", help="re-fetch from AniList and ignore saved progress")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    if args.restart or not RAW_FILE.exists():
        print(f"Fetching {args.pages} pages of ANIME from AniList...")
        anime_entries = fetch_all(args.pages, "ANIME")
        print(f"Fetching {args.pages} pages of MANGA from AniList...")
        manga_entries = fetch_all(args.pages, "MANGA")
        raw_entries = anime_entries + manga_entries
        RAW_FILE.write_text(json.dumps(raw_entries, indent=2))
        print(f"  {len(raw_entries)} entries fetched ({len(anime_entries)} anime, {len(manga_entries)} manga)")
        save_progress(0)
    else:
        raw_entries = json.loads(RAW_FILE.read_text())
        print(f"Resuming with {len(raw_entries)} previously-fetched entries (--pages ignored on resume)")

    start = 0 if args.restart else load_progress()
    if start >= len(raw_entries):
        print("Already complete.")
        raise SystemExit

    cache = load_cache()

    for i in range(start, len(raw_entries), args.batch_size):
        batch = raw_entries[i : i + args.batch_size]
        print(f"Batch {i}-{i + len(batch)} of {len(raw_entries)}...")

        chunks = process(batch, cache, rank_offset=i)
        print(f"  {len(chunks)} chunks embedded")

        # Merge into the full cache and persist all of it -- process()'s own
        # cache_path writing would overwrite the file with only this batch,
        # destroying prior batches' cached embeddings on disk between calls.
        for c in chunks:
            cache[c["source_id"]] = {"chunk_text": c["chunk_text"], "embedding": c["embedding"]}
        CACHE_PATH.write_text(json.dumps([{"source_id": k, **v} for k, v in cache.items()], indent=2))

        count = load(chunks)
        print(f"  {count} rows loaded into Qdrant")

        save_progress(i + len(batch))

    print("Done.")
