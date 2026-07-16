"""Surface recurring corpus gaps from query_log for manual triage before ingesting by name.

Usage: ./.venv/bin/python ingest/review_misses.py
"""
import os
from collections import Counter

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def main() -> None:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    result = client.table("query_log").select("question,route,similarity,result_count,created_at").eq("is_miss", True).execute()
    rows = result.data

    if not rows:
        print("No misses logged yet.")
        return

    counts = Counter(r["question"].strip().lower() for r in rows)
    print(f"{len(rows)} total misses, {len(counts)} distinct questions.\n")
    for question, count in counts.most_common(30):
        example = next(r for r in rows if r["question"].strip().lower() == question)
        detail = f"similarity={example['similarity']:.2f}" if example["similarity"] is not None else f"result_count={example['result_count']}"
        print(f"[{count}x] ({example['route']}, {detail}) {example['question']}")


if __name__ == "__main__":
    main()
