"""OpenAI client for embeddings."""
import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

import tiktoken

from src.config import settings

logger = logging.getLogger(__name__)

# text-embedding-3-* tokenize with cl100k_base
_enc = tiktoken.get_encoding("cl100k_base")

# Request shaping — the embeddings endpoint caps both the number of inputs and
# the total tokens per request. Sending a whole document in one call fails
# outright on large files, so requests are planned to stay under both limits.
_MAX_INPUTS_PER_REQUEST = 96
_MAX_TOKENS_PER_REQUEST = 250_000   # headroom under the 300k ceiling
_MAX_TOKENS_PER_INPUT = 8_191       # hard model limit
_MAX_ATTEMPTS = 3


def _truncate_to_tokens(text: str, limit: int = _MAX_TOKENS_PER_INPUT) -> str:
    """Truncate on a token boundary — character limits under-count dense text."""
    tokens = _enc.encode(text, disallowed_special=())
    if len(tokens) <= limit:
        return text
    return _enc.decode(tokens[:limit])


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
        response = await self.client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=_truncate_to_tokens(text),
        )
        return response.data[0].embedding

    @staticmethod
    def _plan_batches(texts: List[str]) -> List[List[str]]:
        """Group inputs into requests that respect the input and token caps."""
        batches: List[List[str]] = []
        current: List[str] = []
        current_tokens = 0

        for text in texts:
            cost = min(len(_enc.encode(text, disallowed_special=())), _MAX_TOKENS_PER_INPUT)
            too_many = len(current) >= _MAX_INPUTS_PER_REQUEST
            too_big = current and current_tokens + cost > _MAX_TOKENS_PER_REQUEST
            if too_many or too_big:
                batches.append(current)
                current = []
                current_tokens = 0
            current.append(text)
            current_tokens += cost

        if current:
            batches.append(current)
        return batches

    async def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        """Embed one request's worth of inputs, retrying transient failures."""
        last_error: Optional[Exception] = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self.client.embeddings.create(
                    model=settings.OPENAI_EMBEDDING_MODEL,
                    input=batch,
                )
                return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            except Exception as e:
                last_error = e
                if attempt == _MAX_ATTEMPTS:
                    break
                delay = 2 ** (attempt - 1)
                logger.warning(
                    "Embedding request failed (attempt %d/%d), retrying in %ds: %s",
                    attempt, _MAX_ATTEMPTS, delay, e,
                )
                await asyncio.sleep(delay)

        raise RuntimeError(f"Embedding request failed after {_MAX_ATTEMPTS} attempts: {last_error}")

    async def get_embeddings_batch(
        self,
        texts: List[str],
        progress_cb: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> List[List[float]]:
        """Embed *texts*, splitting into as many requests as the limits require.

        ``progress_cb(done_batches, total_batches)`` is awaited after each
        request so long documents can report progress.
        """
        if not texts:
            return []

        prepared = [_truncate_to_tokens(t) for t in texts]
        batches = self._plan_batches(prepared)

        if len(batches) > 1:
            logger.info("Embedding %d chunks across %d requests", len(prepared), len(batches))

        embeddings: List[List[float]] = []
        for i, batch in enumerate(batches, 1):
            embeddings.extend(await self._embed_batch(batch))
            if progress_cb:
                await progress_cb(i, len(batches))

        return embeddings


openai_client = OpenAIClient()
