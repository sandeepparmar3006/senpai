"""Eval harness: precision@k on retrieval + keyword match on generated answers."""
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

TOGETHER_API_KEY = os.environ["TOGETHER_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

EMBED_MODEL = "intfloat/multilingual-e5-large-instruct"
CHAT_MODEL = "openai/gpt-oss-20b"  # open-weight, serverless-accessible on this account
K = 5


def embed_query(text: str) -> list[float]:
    resp = requests.post(
        "https://api.together.xyz/v1/embeddings",
        headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
        json={"model": EMBED_MODEL, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def retrieve(query_embedding: list[float], k: int = K) -> list[dict]:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    result = client.rpc(
        "match_media_chunks", {"query_embedding": query_embedding, "match_count": k}
    ).execute()
    return result.data


def generate_answer(question: str, chunks: list[dict]) -> str:
    context = "\n\n".join(f"[{c['title']}] {c['chunk_text']}" for c in chunks)
    resp = requests.post(
        "https://api.together.xyz/v1/chat/completions",
        headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
        json={
            "model": CHAT_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Answer only using the provided context. Cite the anime title in brackets.",
                },
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def run_eval(qa_pairs: list[dict]) -> None:
    hits, keyword_matches, total = 0, 0, 0
    for pair in qa_pairs:
        if not pair.get("question"):
            continue
        total += 1
        embedding = embed_query(pair["question"])
        chunks = retrieve(embedding)
        retrieved_titles = {c["title"] for c in chunks}
        if pair.get("expected_title") in retrieved_titles:
            hits += 1
        answer = generate_answer(pair["question"], chunks)
        if any(kw.lower() in answer.lower() for kw in pair.get("expected_keywords", [])):
            keyword_matches += 1
        print(f"Q: {pair['question']}\nA: {answer}\n")

    if total == 0:
        print("No filled-in questions in qa_pairs.json yet — nothing to eval.")
        return
    print(f"Retrieval hit rate (expected title in top-{K}): {hits}/{total} = {hits/total:.0%}")
    print(f"Answer keyword match rate: {keyword_matches}/{total} = {keyword_matches/total:.0%}")


if __name__ == "__main__":
    qa_path = Path(__file__).parent / "qa_pairs.json"
    qa_pairs = json.loads(qa_path.read_text())
    run_eval(qa_pairs)
