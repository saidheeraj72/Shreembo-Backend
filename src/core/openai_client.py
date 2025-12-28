"""OpenAI client for embeddings."""
from typing import List
from src.config import settings


class OpenAIClient:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    async def get_embedding(self, text: str) -> List[float]:
        text = text[:30000] if len(text) > 30000 else text
        response = await self.client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=text
        )
        return response.data[0].embedding

    async def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        texts = [t[:30000] for t in texts]
        response = await self.client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=texts
        )
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


openai_client = OpenAIClient()
