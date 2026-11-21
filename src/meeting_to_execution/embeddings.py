from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from .config import Settings


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]: ...
    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedding:
    """Deterministic local embeddings for offline demos and tests.

    This is not a semantic model, but it gives stable lexical retrieval without
    external services. Production deployments should set LLM_API_KEY and use
    OpenAIEmbedding (or swap in another embedding provider).
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    @staticmethod
    def _tokens(text: str) -> list[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        tokens = list(words)
        tokens.extend(f"{a}_{b}" for a, b in zip(words, words[1:], strict=False))
        tokens.extend(
            f"{word[:i]}" for word in words for i in range(3, min(len(word), 8) + 1)
        )
        return tokens

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in self._tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class OpenAIEmbedding:
    def __init__(self, api_key: str, base_url: str, model: str, dimension: int = 1536):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        response = self.client.embeddings.create(model=self.model, input=text)
        vector = response.data[0].embedding
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        vectors = [item.embedding for item in response.data]
        normalized = []
        for vector in vectors:
            norm = math.sqrt(sum(value * value for value in vector))
            normalized.append([value / norm for value in vector] if norm else vector)
        return normalized


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.use_llm:
        try:
            return OpenAIEmbedding(
                api_key=settings.llm_api_key or "",
                base_url=settings.llm_base_url,
                model=settings.embedding_model,
                dimension=settings.embedding_dim,
            )
        except Exception:
            pass
    return HashEmbedding(settings.embedding_dim)
