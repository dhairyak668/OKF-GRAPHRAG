from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from openai import OpenAI

DEFAULT_MODEL = "text-embedding-3-small"
BATCH_SIZE = 64
EMBEDDING_DIM = 1536


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass


def get_client() -> OpenAI:
    _load_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY not set. Add it to .env or export it."
        )
    return OpenAI(api_key=api_key)


def embed_texts(
    texts: list[str],
    client: OpenAI | None = None,
    model: str = DEFAULT_MODEL,
) -> np.ndarray:
    if client is None:
        client = get_client()

    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        response = client.embeddings.create(input=batch, model=model)
        for item in response.data:
            all_embeddings.append(item.embedding)

    return np.array(all_embeddings, dtype=np.float32)


def embed_query(
    text: str,
    client: OpenAI | None = None,
    model: str = DEFAULT_MODEL,
) -> np.ndarray:
    return embed_texts([text], client=client, model=model)[0]
