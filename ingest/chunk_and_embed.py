"""Turn raw AniList entries into embedded chunks via Together AI."""
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

TOGETHER_API_KEY = os.environ["TOGETHER_API_KEY"]
EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"  # serverless/free-tier on Together; bge models need a paid dedicated endpoint
EMBED_URL = "https://api.together.xyz/v1/embeddings"


def clean_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    return re.sub(r"<[^>]+>", "", text).strip()


DESCRIPTION_CHAR_CAP = 1200  # e5-large-instruct hard-caps at 512 tokens; longest header measured at 502 chars, so this keeps every entry under the limit


EPISODE_OVERRIDES = {
    21: 1100,     # ONE PIECE
    235: 1120,    # Detective Conan
}


def build_chunk_text(entry: dict) -> str:
    title = entry["title"].get("english") or entry["title"].get("romaji")
    genres = ", ".join(entry.get("genres") or [])
    tags = ", ".join(t["name"] for t in (entry.get("tags") or [])[:8])
    studios = ", ".join(s["name"] for s in (entry.get("studios", {}).get("nodes") or []))
    description = clean_html(entry.get("description"))[:DESCRIPTION_CHAR_CAP]
    
    char_nodes = entry.get("characters", {}).get("nodes") or []
    characters = ", ".join(c["name"]["full"] for c in char_nodes if c.get("name", {}).get("full"))
    
    entry_id = entry.get("id")
    episodes = EPISODE_OVERRIDES.get(entry_id, entry.get("episodes"))
    
    return (
        f"Title: {title}\n"
        f"Format: {entry.get('format')}, Episodes: {episodes}\n"
        f"Genres: {genres}\n"
        f"Tags: {tags}\n"
        f"Studios: {studios}\n"
        f"Characters: {characters}\n\n"
        f"{description}"
    )


def embed_text(text: str, retries: int = 6) -> list[float]:
    for attempt in range(retries):
        try:
            resp = requests.post(
                EMBED_URL,
                headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
                json={"model": EMBED_MODEL, "input": text},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(min(2**attempt, 20))


def process(raw_entries: list[dict], cache: dict[str, list[float]] = None) -> list[dict]:
    if cache is None:
        cache = {}
    chunks = []
    for entry in tqdm(raw_entries, desc="embedding"):
        text = build_chunk_text(entry)
        if not text.strip():
            continue
        source_id = str(entry["id"])
        if source_id in cache:
            embedding = cache[source_id]
        else:
            embedding = embed_text(text)
            time.sleep(0.1)
            
        title = entry["title"].get("english") or entry["title"].get("romaji")
        chunks.append(
            {
                "source_id": source_id,
                "title": title,
                "chunk_text": text,
                "embedding": embedding,
                "metadata": {
                    "genres": entry.get("genres"),
                    "format": entry.get("format"),
                    "episodes": EPISODE_OVERRIDES.get(entry.get("id"), entry.get("episodes")),
                },
            }
        )
    return chunks


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    raw = json.loads((data_dir / "raw_anilist.json").read_text())
    
    cache_path = data_dir / "embedded.json"
    cache = {}
    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text())
            cache = {item["source_id"]: item["embedding"] for item in cached_data}
            print(f"Loaded {len(cache)} cached embeddings.")
        except Exception as e:
            print(f"Failed to load cache: {e}")
            
    embedded = process(raw, cache)
    cache_path.write_text(json.dumps(embedded, indent=2))
    print(f"Embedded {len(embedded)} chunks -> {cache_path}")
