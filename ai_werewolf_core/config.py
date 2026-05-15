from typing import List, Any, Dict
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field, field_validator, Field, BaseModel, model_validator

class ModelConfig(BaseModel):
    """单个模型的静态配置，支持在 .env 中覆盖"""
    model_id: str = Field(..., description="模型唯一标识")
    provider: str = Field(..., description="提供者名称，如 openai、anthropic")
    name: str = Field(..., description="业务层使用的模型名称")
    api_key: str = Field(..., description="对应提供者的 API Key")
    base_url: str = Field(..., description="API 基础 URL")
    model_name: str = Field(..., description="LLM 实际模型名称")
    temperature: float = Field(0.7, description="默认温度")
    max_tokens: int = Field(1024, description="默认最大 token")
    timeout: float = Field(15.0, description="硬超时（秒）")

class Settings(BaseSettings):
    """
    全局配置类，使用 pydantic-settings 进行强类型校验。
    实际的敏感数据（如 API 密钥）应存放在项目根目录的 .env 文件中，
    .env 文件已经被 .gitignore 忽略，不会提交到 Git 仓库。
    """
    
    # 加密密钥
    crypto_key: str = "default-dev-crypto-key-change-in-prod"
    
    # 数据库配置 (Postgres)
    pg_host: str = "127.0.0.1"
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
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 1
    redis_max_connections: int = 300
    redis_timeout: int = 10
    
    @computed_field
    @property
    def redis_url(self) -> str:
        """自动拼接 Redis 连接串"""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
    
    # 日志与可观测性配置
    environment: str = "dev"

    # LangSmith 链路追踪（可选）
    langsmith_api_key: str = ""
    langsmith_project: str = "werewolf"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # 应用基础配置
    debug: bool = False

    # 雪花算法配置
    snowflake_datacenter_id: int = 1       # 数据中心 ID (0-31)，根据实际部署环境调整
    snowflake_worker_id: int = 1           # 工作节点 ID (0-31)，每个节点/进程需唯一

    # 记忆压缩配置
    compression_model_url: str = "https://api.openai.com/v1"
    compression_model_key: str = ""
    compression_model_name: str = "gpt-3.5-turbo"
    # 模型配置列表（可在 .env 中覆盖或在运行时动态加载）
    models: List[ModelConfig] = Field(
        default_factory=lambda: [
            ModelConfig(
                model_id="default-openai",
                provider="openai",
                name="GPT-4 Turbo",
                api_key="",
                base_url="https://api.openai.com/v1",
                model_name="gpt-4-turbo",
                temperature=0.7,
                max_tokens=1024,
                timeout=15.0,
            )
        ],
        description="系统支持的 LLM 列表，支持运行时动态扩展",
    )

    @model_validator(mode='before')
    @classmethod
    def parse_models_from_env(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """从环境变量中解析 MODEL_X_... 配置"""
        import os
        models_dict = {}
        
        # 遍历所有环境变量和传入的 values
        all_vars = {**os.environ, **values}
        
        for key, value in all_vars.items():
            key_upper = key.upper()
            if key_upper.startswith("MODEL_"):
                parts = key_upper.split("_", 2)
                if len(parts) >= 3 and parts[1].isdigit():
                    idx = int(parts[1])
                    field_name = parts[2].lower()
                    if idx not in models_dict:
                        models_dict[idx] = {}
                    models_dict[idx][field_name] = value
                    
        if models_dict:
            # 如果环境变量中定义了模型，则覆盖默认列表
            parsed_models = []
            for idx in sorted(models_dict.keys()):
                model_data = models_dict[idx]
                # 确保必填字段存在，如果不存在则跳过或报错
                if "model_id" in model_data and "provider" in model_data:
                    parsed_models.append(model_data)
            
            if parsed_models:
                values["models"] = parsed_models
                
        return values

    @field_validator("debug", mode="before")
    @classmethod
    def coerce_debug_bool(cls, v):
        """HACK: 系统环境可能注入非标准布尔字符串（如 'release'），
        必须显式转换，防止 pydantic 校验失败。"""
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        return bool(v)
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

# 实例化全局配置对象
settings = Settings()
