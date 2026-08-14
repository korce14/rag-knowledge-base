from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = PROJECT_ROOT / "data"
    prompt_dir: Path = PROJECT_ROOT / "prompts"

    # 生成模型
    generation_api_key: str = ""
    generation_base_url: str = "https://api.deepseek.com"
    generation_model: str = "deepseek-chat"

    # 嵌入模型
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024

    # 重排模型
    rerank_api_key: str = ""
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Qdrant 向量库
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_prefix: str = "rag"
    qdrant_prefer_grpc: bool = False

    # Redis 缓存与熔断
    redis_url: str = ""
    cache_ttl_seconds: int = 300
    bloom_capacity: int = 100000
    bloom_error_rate: float = 0.01
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout_seconds: int = 30

    # 权限认证
    auth_token: str = ""
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    token_expire_minutes: int = 720
    admin_username: str = ""
    admin_password: str = ""

    # Prompt 版本
    prompt_version: str = "latest"

    # 安全校验
    guard_max_input_length: int = 2000
    guard_max_output_length: int = 8000
    guard_upload_max_bytes: int = 20 * 1024 * 1024

    # 分块与检索
    chunk_size: int = 500
    chunk_overlap: int = 80
    top_k: int = 5
    rrf_k: int = 60

    @property
    def qdrant_enabled(self) -> bool:
        return bool(self.qdrant_url)

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url)

    @property
    def dense_enabled(self) -> bool:
        # 迁移到 Qdrant 后，向量检索依赖嵌入模型和 Qdrant 同时可用。
        return bool(self.embedding_api_key and self.qdrant_url)

    @property
    def generation_enabled(self) -> bool:
        return bool(self.generation_api_key)

    @property
    def rerank_enabled(self) -> bool:
        return bool(self.rerank_api_key)


settings = Settings()

