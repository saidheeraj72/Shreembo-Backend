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
        default=["http://localhost:5173", "http://localhost:8080"]
    )

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    CACHE_ENABLED: bool = True
    CACHE_TTL: int = 300  # 5 minutes

    # Email (SMTP)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = "saidheeraj985@gmail.com"
    SMTP_PASSWORD: Optional[str] = "Sai@17895"
    SMTP_FROM_EMAIL: Optional[str] = "saidheeraj985@gmail.com"
    SMTP_FROM_NAME: str = "Enterprise DMS"
    SMTP_ENABLED: bool = True

    # Frontend
    FRONTEND_URL: str = "http://localhost:5173"

    # Security
    SECRET_KEY: str = Field(default="change-this-in-production-secret-key-min-32-chars")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # File Upload
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_FILE_TYPES: List[str] = Field(
        default=["pdf", "docx", "xlsx", "pptx", "jpg", "png", "txt", "csv"]
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

    # AWS S3
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_S3_BUCKET: Optional[str] = None
    AWS_S3_REGION: str 
    AWS_S3_ENDPOINT_URL: Optional[str] = None  # For S3-compatible services

    # Pinecone
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_INDEX_NAME: str = "document-embeddings"
    PINECONE_CHAT_SESSIONS_INDEX: str = "chat-sessions"

    # OpenAI
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536

    # OpenAI Chat
    OPENAI_CHAT_MODEL: str = "gpt-4.1-mini-2025-04-14"
    OPENAI_CHAT_MAX_TOKENS: int = 4096
    OPENAI_CHAT_TEMPERATURE: float = 0.7

    # RAG Settings
    RAG_TOP_K: int = 5
    RAG_MAX_CONTEXT_LENGTH: int = 8000
    RAG_SYSTEM_PROMPT: str = """You are a helpful AI assistant for an enterprise document management system.

When answering questions based on the provided context, you must:
1. Preserve ALL details exactly as they appear in the retrieved content
2. Maintain the original structure and organization of information
3. Include specific data points, numbers, dates, names, and technical details
4. DO NOT summarize or skip any relevant information from the context
5. Present information in a clear, well-structured format that mirrors the source material
6. If the context contains tables, lists, or structured data, preserve that formatting
7. Cite specific sections or headers when referencing information

Your goal is to provide comprehensive, detailed answers that retain the full richness of the source documents."""

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
        default=["pdf", "docx", "txt", "md"]
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
