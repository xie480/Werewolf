"""
全局 Redis 异步客户端管理器 —— 共享连接池。

**Why**: 项目中多个模块（EventBus、VoteManager、StateMachine、
PlayerStatusManager）都需要 Redis 连接。如果各模块独立创建连接池，
会导致连接数膨胀、资源浪费和配置不一致。本模块提供全局单例连接池，
确保所有 Redis 操作共享同一组连接，统一配置管理。

**连接池配置**:
    所有连接参数从 :class:`ai_werewolf_core.config.Settings` 加载，
    包括 host、port、db、max_connections、socket_timeout。

**生命周期**:
    - :meth:`get_client`: 懒初始化，首次调用时创建连接池
    - :meth:`close`: 在应用 shutdown 时调用，释放所有连接

**健康检查**:
    :meth:`health_check` 通过 PING 命令验证 Redis 可用性，
    用于 Kubernetes 就绪探针或负载均衡健康检查。

使用示例::

    from ai_werewolf_core.utils.redis_client import RedisClientManager

    # 获取客户端
    client = await RedisClientManager.get_client()

    # 健康检查
    is_healthy = await RedisClientManager.health_check()

    # 应用关闭时
    await RedisClientManager.close()
"""

import asyncio
from typing import Optional

import redis.asyncio as aioredis

from ai_werewolf_core.config import settings
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 健康检查超时 (秒)
HEALTH_CHECK_TIMEOUT_SEC: float = 2.0


# ============================================================================
# 异常定义
# ============================================================================

class RedisClientNotInitializedError(Exception):
    """Redis 客户端尚未初始化异常。

    当调用方在 :meth:`RedisClientManager.get_client` 之前尝试使用
    客户端或连接池时抛出。
    """

    def __init__(self, message: str = "Redis 客户端尚未初始化，请先调用 get_client()"):
        super().__init__(message)


# ============================================================================
# 全局 Redis 客户端管理器
# ============================================================================

class RedisClientManager:
    """全局 Redis 异步客户端管理器。

    单例模式管理 redis.asyncio.ConnectionPool 和 Redis 客户端实例。
    所有模块通过此类获取同一个连接池的客户端引用，
    避免连接数膨胀和配置碎片化。

    线程安全:
        使用 :class:`asyncio.Lock` 保护 ``_pool`` 和 ``_client`` 的初始化，
        支持多协程并发调用 :meth:`get_client`。

    Attributes:
        _pool: 全局连接池实例 (懒初始化)。
        _client: 全局 Redis 客户端实例 (懒初始化)。
        _initialized: 标记是否已完成初始化。
        _lock: 异步锁，保护初始化竞态。
    """

    _pool: Optional[aioredis.ConnectionPool] = None
    _client: Optional[aioredis.Redis] = None
    _initialized: bool = False
    _lock: Optional[asyncio.Lock] = None

    # ------------------------------------------------------------------
    # 公开类方法
    # ------------------------------------------------------------------

    @classmethod
    async def get_client(cls) -> aioredis.Redis:
        """获取全局 Redis 异步客户端。

        首次调用时懒初始化连接池和客户端。后续调用返回同一客户端实例。
        使用双重检查锁定模式确保线程安全。

        Returns:
            已配置的 :class:`redis.asyncio.Redis` 客户端实例。

        Raises:
            redis.exceptions.ConnectionError: Redis 服务不可达。
        """
        import asyncio
        try:
            current_loop = id(asyncio.get_running_loop())
            logger.info(f"[DIAGNOSIS] get_client called. Current loop: {current_loop}")
        except RuntimeError:
            current_loop = "None"
            logger.info("[DIAGNOSIS] get_client called. No running loop!")

        if cls._initialized and cls._client is not None:
            try:
                logger.info(f"[DIAGNOSIS] Returning cached client to loop: {current_loop}. Is loop closed? {asyncio.get_running_loop().is_closed()}")
            except RuntimeError:
                pass
            return cls._client

        if cls._lock is None:
            cls._lock = asyncio.Lock()

        async with cls._lock:
            # 双重检查：锁内再次确认未被其他协程抢先初始化
            if cls._initialized and cls._client is not None:
                return cls._client

            cls._pool = cls._create_pool()
            cls._client = cls._create_client(cls._pool)
            
            try:
                init_loop = id(asyncio.get_running_loop())
                logger.info(f"[DIAGNOSIS] Client initialized in loop: {init_loop}")
            except RuntimeError:
                pass

            # 验证连接可用性
            try:
                await cls._client.ping()
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                logger.error(
                    "Redis 连接验证失败 (PING)",
                    host=settings.redis_host,
                    port=settings.redis_port,
                    error=str(e),
                    exc_info=True,
                )
                # 清理失败的状态，允许后续重试
                cls._pool = None
                cls._client = None
                raise

            cls._initialized = True
            logger.info(
                "Redis 客户端管理器初始化完成",
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                max_connections=settings.redis_max_connections,
            )

        return cls._client

    @classmethod
    async def health_check(cls) -> bool:
        """Redis 健康检查 —— 通过 PING 命令验证可用性。

        **Why**: 用于 Kubernetes 就绪探针 (/healthz) 或负载均衡器
        健康检查端点。不抛出异常，返回布尔值表示可用性。

        Returns:
            ``True`` 如果 Redis 正常响应 PING，否则 ``False``。
        """
        if cls._client is None:
            return False

        try:
            result = await asyncio.wait_for(
                cls._client.ping(),
                timeout=HEALTH_CHECK_TIMEOUT_SEC,
            )
            return result is True
        except (asyncio.TimeoutError, aioredis.ConnectionError, aioredis.TimeoutError):
            logger.warning("Redis 健康检查失败", exc_info=True)
            return False
        except Exception:
            logger.error("Redis 健康检查异常", exc_info=True)
            return False

    @classmethod
    async def close(cls) -> None:
        """关闭 Redis 连接池和客户端，释放所有连接资源。

        **Why**: 在应用 graceful shutdown 时必须调用，
        否则会导致连接泄漏和进程挂起。应在 FastAPI shutdown 事件中注册。

        幂等操作：重复调用不会出错。
        """
        if cls._lock is None:
            cls._lock = asyncio.Lock()
            
        async with cls._lock:
            if cls._client is not None:
                try:
                    await cls._client.aclose()
                    logger.info("Redis 客户端已关闭")
                except Exception as e:
                    logger.warning("关闭 Redis 客户端时出现异常", error=str(e))

            if cls._pool is not None:
                try:
                    await cls._pool.disconnect()
                    logger.info("Redis 连接池已释放")
                except Exception as e:
                    logger.warning("释放 Redis 连接池时出现异常", error=str(e))

            cls._client = None
            cls._pool = None
            cls._initialized = False

    @classmethod
    async def reset(cls) -> None:
        """重置 Redis 客户端管理器状态（仅用于测试）。

        **Why**: 测试环境中需要在每个测试用例前后清理状态，
        确保测试隔离性。生产代码不应调用此方法。
        """
        await cls.close()
        logger.debug("Redis 客户端管理器已重置")

    # ------------------------------------------------------------------
    # 内部工厂方法
    # ------------------------------------------------------------------

    @staticmethod
    def _create_pool() -> aioredis.ConnectionPool:
        """创建 Redis 异步连接池。

        连接参数从全局 :class:`Settings` 配置加载，
        使用 ``decode_responses=True`` 统一返回字符串类型。

        Returns:
            配置好的 :class:`redis.asyncio.ConnectionPool` 实例。
        """
        pool = aioredis.ConnectionPool(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            max_connections=settings.redis_max_connections,
            socket_timeout=settings.redis_timeout,
            socket_connect_timeout=settings.redis_timeout,
            decode_responses=True,
        )
        logger.debug(
            "Redis 连接池已创建",
            max_connections=settings.redis_max_connections,
            socket_timeout=settings.redis_timeout,
        )
        return pool

    @staticmethod
    def _create_client(pool: aioredis.ConnectionPool) -> aioredis.Redis:
        """基于给定连接池创建 Redis 客户端。

        Args:
            pool: 已配置的连接池实例。

        Returns:
            :class:`redis.asyncio.Redis` 客户端实例。
        """
        return aioredis.Redis(connection_pool=pool)
