from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

class Settings(BaseSettings):
    """
    全局配置类，使用 pydantic-settings 进行强类型校验。
    实际的敏感数据（如 API 密钥）应存放在项目根目录的 .env 文件中，
    .env 文件已经被 .gitignore 忽略，不会提交到 Git 仓库。
    """
    
    # AI 模型配置 (OpenAI 兼容)
    openai_api_base: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    model_name: str = "gpt-4-turbo"
    
    # 数据库配置 (Postgres)
    pg_host: str = "192.168.100.128"
    pg_port: int = 5432
    pg_user: str = ""
    pg_password: str = ""
    pg_database: str = ""
    
    @computed_field
    @property
    def database_url(self) -> str:
        """自动拼接 SQLAlchemy 异步连接串"""
        return f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
    
    # Redis 配置
    redis_host: str = "192.168.100.128"
    redis_port: int = 6379
    redis_db: int = 1
    redis_max_connections: int = 300
    redis_timeout: int = 10
    
    @computed_field
    @property
    def redis_url(self) -> str:
        """自动拼接 Redis 连接串"""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
    
    # 应用基础配置
    debug: bool = False
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

# 实例化全局配置对象
settings = Settings()
