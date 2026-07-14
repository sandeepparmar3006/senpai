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

LORE_OVERRIDES = {
    21: "Monkey D. Luffy ate the Gomu Gomu no Mi (Gum-Gum Fruit), a Paramecia-type Devil Fruit that gives him rubber powers.",
    235: "Conan Edogawa is actually Shinichi Kudo, shrunken by the APTX 4869 drug created by the Black Organization.",
}


def generate_chunks(entry: dict) -> list[dict]:
    chunks = []
    entry_id = entry.get("id")
    title = entry["title"].get("english") or entry["title"].get("romaji")
    
    # MAIN CHUNK
    genres = ", ".join(entry.get("genres") or [])
    tags = ", ".join(t["name"] for t in (entry.get("tags") or [])[:8])
    studios = ", ".join(s["name"] for s in (entry.get("studios", {}).get("nodes") or []))
    episodes = EPISODE_OVERRIDES.get(entry_id, entry.get("episodes"))
    synonyms = ", ".join(entry.get("synonyms") or [])
    season = f"{entry.get('season') or ''} {entry.get('seasonYear') or ''}".strip()
    
    lore = LORE_OVERRIDES.get(entry_id, "")
    
    main_header = (
        f"Title: {title}\n"
        f"Synonyms: {synonyms}\n"
        f"Format: {entry.get('format')}, Episodes: {episodes}\n"
        f"Season: {season}\n"
        f"Genres: {genres}\n"
        f"Tags: {tags}\n"
        f"Studios: {studios}\n"
    )
    if lore:
        main_header += f"Lore: {lore}\n"
    main_header += "\n"
    
    # Keep total chars under ~1300
    max_desc_len = max(250, 1300 - len(main_header))
    description = clean_html(entry.get("description"))[:max_desc_len]
    main_text = main_header + description
    
    chunks.append({
        "source_id": str(entry_id),
        "title": title,
        "chunk_text": main_text,
        "metadata": {
            "genres": entry.get("genres"),
            "format": entry.get("format"),
            "episodes": episodes,
            "anilist_id": entry_id,
        }
    })
    
    # CAST & STAFF CHUNK
    staff_nodes = entry.get("staff", {}).get("nodes") or []
    staff = ", ".join(s["name"]["full"] for s in staff_nodes if s.get("name", {}).get("full"))
    
    char_edges = entry.get("characters", {}).get("edges") or []
    char_list = []
    for edge in char_edges:
        name = edge.get("node", {}).get("name", {}).get("full", "")
        role = edge.get("role", "")
        if name:
            char_list.append(f"{name} ({role})" if role else name)
    characters = ", ".join(char_list)
    
    cast_text = f"Title: {title}\nStaff: {staff}\nCharacters: {characters}"
    cast_text = cast_text[:1300]
    if staff or characters:
        chunks.append({
            "source_id": f"{entry_id}_cast",
            "title": title,
            "chunk_text": cast_text,
            "metadata": {
                "anilist_id": entry_id
            }
        })
    
    # LORE CHUNKS (Character descriptions)
    current_lore_chunk = f"Title: {title} - Character Lore\n\n"
    lore_index = 1
    
    for edge in char_edges:
        node = edge.get("node", {})
        name = node.get("name", {}).get("full", "")
        desc = clean_html(node.get("description"))
        if name and desc:
            char_text = f"Character: {name}\n{desc}\n\n"
            if len(current_lore_chunk) + len(char_text) > 1300:
                if current_lore_chunk != f"Title: {title} - Character Lore\n\n":
                    chunks.append({
                        "source_id": f"{entry_id}_lore_{lore_index}",
                        "title": title,
                        "chunk_text": current_lore_chunk[:1300],
                        "metadata": {"anilist_id": entry_id}
                    })
                    lore_index += 1
                # start new chunk
                current_lore_chunk = f"Title: {title} - Character Lore\n\n"
                if len(char_text) > 1200:
                    char_text = char_text[:1200]
                
            current_lore_chunk += char_text
            
    if current_lore_chunk != f"Title: {title} - Character Lore\n\n":
        chunks.append({
            "source_id": f"{entry_id}_lore_{lore_index}",
            "title": title,
            "chunk_text": current_lore_chunk[:1300],
            "metadata": {"anilist_id": entry_id}
        })
        
    return chunks


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


def process(raw_entries: list[dict], cache: dict = None) -> list[dict]:
    if cache is None:
        cache = {}
    embedded_chunks = []
    for entry in tqdm(raw_entries, desc="embedding"):
        chunks_for_entry = generate_chunks(entry)
        
        for chunk in chunks_for_entry:
            text = chunk["chunk_text"]
            if not text.strip():
                continue
            source_id = chunk["source_id"]
            
            cached_item = cache.get(source_id)
            if cached_item and isinstance(cached_item, dict) and cached_item.get("chunk_text") == text:
                embedding = cached_item["embedding"]
            elif cached_item and not isinstance(cached_item, dict):
                # Fallback for simple ID -> embedding dict
                embedding = cached_item
            else:
                embedding = embed_text(text)
                time.sleep(0.1)
                
            chunk["embedding"] = embedding
            embedded_chunks.append(chunk)
            
    return embedded_chunks


if __name__ == "__main__":
    data_dir = Path(__file__).parent.parent / "data"
    raw = json.loads((data_dir / "raw_anilist.json").read_text())
    
    cache_path = data_dir / "embedded.json"
    cache = {}
    if cache_path.exists():
        try:
            cached_data = json.loads(cache_path.read_text())
            cache = {item["source_id"]: {"chunk_text": item.get("chunk_text"), "embedding": item["embedding"]} for item in cached_data}
            print(f"Loaded {len(cache)} cached embeddings.")
        except Exception as e:
            print(f"Failed to load cache: {e}")
            
    embedded = process(raw, cache)
    cache_path.write_text(json.dumps(embedded, indent=2))
    print(f"Embedded {len(embedded)} chunks -> {cache_path}")
