"""Application settings using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # DeepSeek LLM Configuration
    deepseek_api_key: str = Field(alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-v4-flash", alias="DEEPSEEK_MODEL")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        alias="DEEPSEEK_BASE_URL",
    )
    deepseek_timeout: float = Field(default=120.0, alias="DEEPSEEK_TIMEOUT")
    deepseek_max_retries: int = Field(default=2, ge=0, alias="DEEPSEEK_MAX_RETRIES")
    deepseek_retry_initial_delay: float = Field(
        default=1.0, ge=0, alias="DEEPSEEK_RETRY_INITIAL_DELAY"
    )
    deepseek_retry_multiplier: float = Field(
        default=2.0, ge=1, alias="DEEPSEEK_RETRY_MULTIPLIER"
    )
    deepseek_retry_max_delay: float = Field(
        default=8.0, ge=0, alias="DEEPSEEK_RETRY_MAX_DELAY"
    )
    deepseek_fallback_model: str = Field(
        default="deepseek-chat", alias="DEEPSEEK_FALLBACK_MODEL"
    )
    deepseek_fallback_api_key: str | None = Field(
        default=None, alias="DEEPSEEK_FALLBACK_API_KEY"
    )
    deepseek_fallback_base_url: str | None = Field(
        default=None, alias="DEEPSEEK_FALLBACK_BASE_URL"
    )

    # 独立 OpenAI 兼容 Embedding 服务；DeepSeek Chat 不提供向量接口。
    embedding_api_key: str | None = Field(default=None, alias="EMBEDDING_API_KEY")
    embedding_model: str = Field(default="", alias="EMBEDDING_MODEL")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_max_attempts: int = Field(default=3, ge=1, le=10, alias="EMBEDDING_MAX_ATTEMPTS")
    embedding_timeout: float = Field(default=5.0, gt=0, alias="EMBEDDING_TIMEOUT")
    embedding_circuit_cooldown: float = Field(default=60.0, gt=0, alias="EMBEDDING_CIRCUIT_COOLDOWN")
    embedding_dimension: int = Field(
        default=1536,
        ge=1,
        alias="EMBEDDING_DIMENSION",
    )

    # Dedicated Cross-Encoder reranker for the second RAG stage.
    reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3",
        alias="RERANKER_MODEL",
    )
    reranker_device: str = Field(default="auto", alias="RERANKER_DEVICE")
    reranker_batch_size: int = Field(default=4, ge=1, le=64, alias="RERANKER_BATCH_SIZE")
    reranker_max_length: int = Field(
        default=512,
        ge=64,
        le=4096,
        alias="RERANKER_MAX_LENGTH",
    )
    reranker_cache_dir: Path = Field(
        default=Path(__file__).resolve().parents[1] / "runtime" / "models",
        alias="RERANKER_CACHE_DIR",
    )
    reranker_allow_heuristic_fallback: bool = Field(
        default=True,
        alias="RERANKER_ALLOW_HEURISTIC_FALLBACK",
    )

    # MCP Configuration
    mcp_servers_config: Path = Field(
        default=Path(__file__).parent / "mcp_servers.json",
        alias="MCP_SERVERS_CONFIG"
    )
    
    # Weather API
    openweather_api_key: str | None = Field(default=None, alias="OPENWEATHER_API_KEY")
    
    # MySQL is authoritative for checkpoints, conversations and preferences.
    mysql_url: str = Field(default="", alias="MYSQL_URL")
    mysql_echo: bool = Field(default=False, alias="MYSQL_ECHO")

    # Background full-snapshot preference extraction.
    preference_extraction_interval_seconds: int = Field(
        default=7200, ge=60, alias="PREFERENCE_EXTRACTION_INTERVAL_SECONDS"
    )
    preference_extraction_user_limit: int = Field(
        default=100, ge=1, le=1000, alias="PREFERENCE_EXTRACTION_USER_LIMIT"
    )
    preference_extraction_message_limit: int = Field(
        default=200, ge=1, le=1000, alias="PREFERENCE_EXTRACTION_MESSAGE_LIMIT"
    )

    # Milvus (long-term memory)
    milvus_host: str = Field(default="localhost", alias="MILVUS_HOST")
    milvus_port: int = Field(default=19530, alias="MILVUS_PORT")
    milvus_api_key: str | None = Field(default=None, alias="MILVUS_API_KEY")
    semantic_cache_similarity_threshold: float = Field(
        default=0.96, ge=0.0, le=1.0, alias="SEMANTIC_CACHE_SIMILARITY_THRESHOLD"
    )
    semantic_cache_ttl_seconds: int = Field(
        default=604800, ge=60, alias="SEMANTIC_CACHE_TTL_SECONDS"
    )
    semantic_cache_knowledge_version: str = Field(
        default="digital-support-v1",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        alias="SEMANTIC_CACHE_KNOWLEDGE_VERSION",
    )

    # Neo4j compatibility graph.
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="password", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")
    
    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    
    @field_validator("deepseek_api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate API key is not empty."""
        if not v or v.strip() == "":
            raise ValueError("DEEPSEEK_API_KEY cannot be empty")
        return v.strip()
    
    def get_model_config(self) -> dict[str, Any]:
        """Get model configuration for LangChain."""
        config: dict[str, Any] = {
            "model": self.deepseek_model,
            "api_key": self.deepseek_api_key,
            "base_url": self.deepseek_base_url,
        }
        return config


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
