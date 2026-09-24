"""Cloudflare Workers AI bge-m3 embeddings (free tier, 1024-dim). Shared by ingest, eval, and the migration."""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

MODEL = "@cf/baai/bge-m3"
BATCH = 25


class DailyCapReached(Exception):
    pass


def embed_texts(texts: list[str], retries: int = 4) -> list[list[float]]:
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/run/{MODEL}"
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        for attempt in range(retries):
            r = requests.post(url, headers=headers, json={"text": batch}, timeout=90)
            if r.status_code == 200:
                break
            if "daily free allocation" in r.text:
                raise DailyCapReached(r.text[:300])
            if r.status_code not in (429, 500, 502, 503) or attempt == retries - 1:
                raise RuntimeError(f"Cloudflare embed {r.status_code}: {r.text[:300]}")
            time.sleep(2**attempt)
        vecs = r.json()["result"]["data"]
        if len(vecs) != len(batch):
            raise RuntimeError(f"Cloudflare returned {len(vecs)} vectors for {len(batch)} texts")
        out.extend(vecs)
    return out


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
