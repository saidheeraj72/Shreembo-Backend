"""
Application configuration and settings.
"""
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    # Application
    PROJECT_NAME: str = "Enterprise Document Management"
    API_VERSION: str = "v1"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = Field(
        default=[
            "http://localhost:5173",
            "http://localhost:8080",
            "https://shreembo.com",
            "https://www.shreembo.com",
            "https://enterprise-intelligence-hub.pages.dev"
        ]
    )

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v):
        if isinstance(v, str):
            origins = [origin.strip() for origin in v.split(",")]
        else:
            origins = v
        
        # Force include production domains
        required_origins = [
            "https://shreembo.com",
            "https://www.shreembo.com",
            "https://enterprise-intelligence-hub.pages.dev"
        ]
        
        for origin in required_origins:
            if origin not in origins:
                origins.append(origin)
                
        return origins

    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_JWT_JWK: Optional[str] = None

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    CACHE_ENABLED: bool = True
    CACHE_TTL: int = 300  # 5 minutes

    # Email (Resend)
    RESEND_API_KEY: Optional[str] = None
    RESEND_FROM_EMAIL: str = "noreply@shreembo.com"
    RESEND_FROM_NAME: str = "Shreembo"
    EMAIL_ENABLED: bool = True

    # Frontend
    FRONTEND_URL: str = "http://localhost:5173"

    # File Upload
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_FILE_TYPES: List[str] = Field(
        default=["pdf", "docx", "xlsx", "pptx", "jpg", "png", "txt", "csv", "md", "html", "json", "xml"]
    )

    @validator("ALLOWED_FILE_TYPES", pre=True)
    def parse_file_types(cls, v):
        if isinstance(v, str):
            return [ft.strip() for ft in v.split(",")]
        return v

    # Audit & Compliance
    AUDIT_LOG_RETENTION_DAYS: int = 90
    AUDIT_LOG_ASYNC: bool = True

    # Invitations
    INVITATION_EXPIRY_DAYS: int = 7

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = None

    # Supabase Storage
    SUPABASE_STORAGE_BUCKET: str = "shreembo"

    # Qdrant
    QDRANT_PATH: str = "./qdrant_data"
    QDRANT_MAIN_COLLECTION: str = "document-embeddings"
    QDRANT_SESSIONS_COLLECTION: str = "chat-sessions"

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536

    # OpenAI Chat
    OPENAI_CHAT_MODEL: str = "gpt-5.4-nano-2026-03-17"
    OPENAI_CHAT_MAX_TOKENS: int = 4096

    # RAG Settings
    RAG_TOP_K: int = 8
    RAG_MAX_CONTEXT_LENGTH: int = 24000       # token budget for assembled context
    RAG_RETRIEVAL_TOP_K_MULTIPLIER: int = 3   # over-fetch factor for reranking
    RAG_MIN_SCORE: float = 0.20               # cosine similarity floor (dense-only results)
    RAG_RERANK_MIN_SCORE: float = 0.05        # cross-encoder relevance floor
    RAG_NEIGHBOR_EXPANSION: int = 3           # top hits to widen with adjacent chunks
    RAG_MAX_TOOL_ROUNDS: int = 2              # tool-calling rounds before answering
    RERANKER_CACHE_DIR: str = "./.model_cache/flashrank"
    # Reasoning effort for the answer itself. Grounding is a thinking problem
    # first: the cheapest way to cut hallucination is to have the model reason
    # over the passages properly, not to patch the answer afterwards.
    RAG_REASONING_EFFORT: str = "medium"

    # Hallucination check — runs after the answer streams, before it is
    # persisted. It ONLY reports which claims the retrieved sources fail to
    # support; it never rewrites the answer and never touches the source list.
    # Runs on every RAG answer regardless of length. Fails open: any error or
    # timeout means no verdict, and the answer is untouched either way.
    RAG_JUDGE_ENABLED: bool = True
    RAG_JUDGE_MODEL: Optional[str] = None     # defaults to OPENAI_CHAT_MODEL
    RAG_JUDGE_TIMEOUT: float = 25.0           # seconds before giving up on a verdict
    RAG_JUDGE_REASONING_EFFORT: str = "high"  # careful reading is the whole job
    RAG_SYSTEM_PROMPT: str = """You are a helpful AI assistant for an enterprise document management system.

Answer the question that was asked — nothing more:
- Give the user exactly what they asked for, then stop. Do not append summaries,
  background, related findings, next steps, or "you may also want to know" material
  they did not ask for.
- Match the length to the question. If it has a one-line answer, give one line. Never
  pad a short answer to look more thorough.
- No preamble and no sign-off. Skip "Great question", "Based on the provided context",
  "Here is what I found", and "Let me know if you need anything else".
- Lead with the answer. If a caveat is genuinely needed to keep it correct, it goes
  after the answer, briefly.
- Be exact about the details you do report: quote figures, dates, names, IDs and
  quantities exactly as the source states them. Never round, reformat or approximate.
- Use only the formatting the answer needs. Prose for a prose answer; a list only when
  the answer is genuinely a list; a table only when the source data is tabular or the
  user asked for one. Do not put headings on a short answer.
- Reproduce a document at length ONLY when the user actually asks for a summary,
  overview, or full extract — and then keep its structure, tables and detail intact.

Grounding rules — these override everything above:
- Use ONLY the provided context and the conversation history. Never invent document
  names, figures, dates, quotes, or sections that are not present in the context.
- If the context does not contain the answer, say so plainly and state what is
  missing, rather than guessing or filling the gap from general knowledge.
- If no context was provided at all, answer from the conversation history only, and
  say that a search of the user's documents turned up nothing for this question.
- An empty or unhelpful context tells you the search found nothing — it tells you
  NOTHING about permissions. Never say you lack access to a document, that a document
  is restricted, or that you cannot open it. You have no information about access.
- If a note says content was omitted because the context budget was reached, tell the
  user your view of the document is partial.

Citations:
- Context sections are numbered "Source 1", "Source 2", and so on. When a statement
  comes from one of them, put the matching marker inline at the end of the sentence,
  like [1] or [2][5].
- Cite only numbers that actually appear in the provided context. Never invent a
  citation number, and never cite when the statement does not come from a source.
- Do not add a bibliography or a list of sources at the end — the interface renders
  the numbered sources for the user.

Your goal is a precise, fully grounded answer to the question that was asked — not a
comprehensive report on the documents."""

    # Email Agent — Gmail (Google OAuth)
    GMAIL_CLIENT_ID: Optional[str] = None
    GMAIL_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/email-agent/oauth/google/callback"
    GMAIL_SCOPES: List[str] = Field(
        default=[
            # modify = read + label/draft changes (mark read, archive, star,
            # trash, labels, drafts) without permanent delete. Supersedes readonly.
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/userinfo.email",
        ]
    )
    # Optional dedicated Fernet key for encrypting stored OAuth tokens.
    # If unset, a key is derived deterministically from SUPABASE_JWT_SECRET.
    EMAIL_AGENT_ENCRYPTION_KEY: Optional[str] = None
    EMAIL_AGENT_OAUTH_STATE_TTL: int = 600  # seconds

    # Serper Web Search
    SERPER_API_KEY: Optional[str] = None
    SERPER_SEARCH_ENDPOINT: str = "https://google.serper.dev/search"
    SERPER_MAX_RESULTS: int = 5

    # Chat Settings
    CHAT_HISTORY_LIMIT: int = 20

    # Document Processing
    ENABLE_EMBEDDINGS: bool = True
    MAX_EMBEDDING_FILE_SIZE_MB: int = 50
    SUPPORTED_EMBEDDING_TYPES: List[str] = Field(
        default=["pdf", "docx", "xlsx", "pptx", "txt", "md", "csv", "html", "json", "xml"]
    )

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    @property
    def api_prefix(self) -> str:
        """Get API prefix."""
        return f"/api/{self.API_VERSION}"

    @property
    def redis_url(self) -> str:
        """Get Redis URL."""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


# Create global settings instance
settings = Settings()
