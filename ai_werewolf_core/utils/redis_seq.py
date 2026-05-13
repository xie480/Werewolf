"""
Redis 全局时序发号器 —— 为事件 Event 提供全局唯一的 seq_num。

**Why**: EventBus 原有的内存自增 `_generate_seq_num` 存在两个严重缺陷:
    1. 进程重启后计数归零 —— seq_num 无法作为可靠的时序依据
    2. 多进程部署时各进程独立计数 —— 无法保证全局唯一递增

基于 Redis String 的 `INCR` 命令天然原子性，保证高并发下的全局唯一递增。

**Redis Key 命名规范**::

    werewolf:seq:{game_id}

**原子递增原理**:
    Redis `INCR` 命令是单线程模型中的原子操作。
    多个客户端并发执行 `INCR werewolf:seq:{game_id}` 时，
    Redis 串行处理，每个请求获得严格递增的返回值，无竞态条件。

**异常处理策略**:
    - Redis 不可用时抛出 `RedisUnavailableException`
    - 调用方应在 EventBus 层捕获此异常，视情况降级为本地计数器或阻塞重试

使用示例::

    from ai_werewolf_core.utils.redis_seq import get_redis_seq

    seq_gen = get_redis_seq()

    # 初始化对局序号 (首次对局时调用)
    await seq_gen.init_seq("game_001")

    # 获取下一个 seq_num
    next_seq = await seq_gen.next_seq("game_001")
    # next_seq = 1, 2, 3, ...

    # 查询当前 seq_num (不递增)
    current = await seq_gen.get_current_seq("game_001")
"""

import asyncio
from typing import Optional

import redis.asyncio as aioredis

from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 默认重试配置
DEFAULT_RETRY_COUNT: int = 3
DEFAULT_RETRY_DELAY_SEC: float = 0.1


# ============================================================================
# 异常定义
# ============================================================================

class RedisUnavailableException(Exception):
    """Redis 不可用异常 —— 发号器无法执行原子递增操作。

    可能原因:
        - Redis 服务宕机或网络不通
        - 连接池耗尽
        - 认证失败
        - 重试全部失败
    """

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        self.original_error = original_error
        super().__init__(message)


# ============================================================================
# Redis 时序发号器
# ============================================================================

class RedisSeqGenerator:
    """基于 Redis INCR 命令的全局唯一递增 seq_num 发号器。

    每个对局 (game_id) 拥有独立的 Redis Key，序号从 1 开始递增。
    使用 redis.asyncio 异步客户端 + 连接池管理。

    Args:
        redis_client: 预先创建的 redis.asyncio.Redis 客户端。
                      如果为 None，则从 get_redis_seq() 工厂函数内部创建连接池。
    """

    def __init__(self, redis_client: Optional[aioredis.Redis] = None):
        self._redis: Optional[aioredis.Redis] = redis_client
        self._owns_client: bool = redis_client is None
        self._closed: bool = False

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def next_seq(self, game_id: str) -> int:
        """获取下一个全局递增 seq_num。

        使用 Redis `INCR` 命令原子递增指定 game_id 的序列号。
        若该 Key 不存在，Redis 自动从 0 开始递增，首次调用返回 1。

        Args:
            game_id: 对局唯一标识

        Returns:
            递增后的 seq_num (从 1 开始)

        Raises:
            RedisUnavailableException: Redis 不可用或操作失败
            ValueError: game_id 为空字符串
        """
        if not game_id or not game_id.strip():
            raise ValueError("game_id 不能为空")

        client = await self._get_client()
        key = self._build_key(game_id)

        for attempt in range(1, DEFAULT_RETRY_COUNT + 1):
            try:
                seq_num = int(await client.incr(key))
                logger.debug(
                    "seq_num 生成成功",
                    game_id=game_id,
                    seq_num=seq_num,
                    key=key,
                )
                return seq_num
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                logger.warning(
                    "Redis 连接异常，重试中",
                    game_id=game_id,
                    attempt=attempt,
                    max_retries=DEFAULT_RETRY_COUNT,
                    error=str(e),
                )
                if attempt < DEFAULT_RETRY_COUNT:
                    await asyncio.sleep(DEFAULT_RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"无法为 game_id={game_id} 生成 seq_num，"
                        f"Redis 重试 {DEFAULT_RETRY_COUNT} 次后仍不可用"
                    ) from e
            except aioredis.ResponseError as e:
                logger.error(
                    "Redis 响应异常",
                    game_id=game_id,
                    error=str(e),
                    exc_info=True,
                )
                raise RedisUnavailableException(
                    f"Redis 返回错误响应: {e}"
                ) from e

        # 理论上不会到达此处
        raise RedisUnavailableException(
            f"为 game_id={game_id} 生成 seq_num 失败 (未知原因)"
        )

    async def init_seq(self, game_id: str, start: int = 1) -> None:
        """初始化对局的序列号计数器。

        使用 `SETNX` 仅在该 Key 不存在时设置初值，
        保证不会覆盖已存在的计数器（幂等操作）。

        Args:
            game_id: 对局唯一标识
            start: 序列号起始值，默认为 1

        Raises:
            RedisUnavailableException: Redis 不可用
            ValueError: game_id 为空或 start < 0
        """
        if not game_id or not game_id.strip():
            raise ValueError("game_id 不能为空")
        if start < 0:
            raise ValueError(f"start 不能为负数: {start}")

        client = await self._get_client()
        key = self._build_key(game_id)

        try:
            # SETNX: 仅当 key 不存在时设置，防止覆盖已有计数器
            result = await client.setnx(key, start)
            if result:
                logger.info(
                    "seq_num 计数器已初始化",
                    game_id=game_id,
                    start=start,
                    key=key,
                )
            else:
                logger.info(
                    "seq_num 计数器已存在，跳过初始化",
                    game_id=game_id,
                    key=key,
                )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法初始化 game_id={game_id} 的 seq_num 计数器"
            ) from e
        except aioredis.ResponseError as e:
            raise RedisUnavailableException(
                f"Redis 返回错误响应: {e}"
            ) from e

    async def get_current_seq(self, game_id: str) -> Optional[int]:
        """查询当前 seq_num 值 (不递增)。

        Args:
            game_id: 对局唯一标识

        Returns:
            当前序列号；如果 Key 不存在则返回 None

        Raises:
            RedisUnavailableException: Redis 不可用
            ValueError: game_id 为空
        """
        if not game_id or not game_id.strip():
            raise ValueError("game_id 不能为空")

        client = await self._get_client()
        key = self._build_key(game_id)

        try:
            value = await client.get(key)
            if value is None:
                return None
            return int(value)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法查询 game_id={game_id} 的 seq_num"
            ) from e
        except aioredis.ResponseError as e:
            raise RedisUnavailableException(
                f"Redis 返回错误响应: {e}"
            ) from e

    async def reset_seq(self, game_id: str) -> None:
        """重置指定对局的序列号计数器（危险操作，仅用于运维）。

        Args:
            game_id: 对局唯一标识

        Raises:
            RedisUnavailableException: Redis 不可用
        """
        client = await self._get_client()
        key = self._build_key(game_id)

        try:
            await client.delete(key)
            logger.warning(
                "seq_num 计数器已重置 (危险操作)",
                game_id=game_id,
                key=key,
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法重置 game_id={game_id} 的 seq_num 计数器"
            ) from e

    async def close(self) -> None:
        """标记发号器为已关闭状态。

        **Why**: 由于现在共享 RedisClientManager 的连接池，
        此方法不再关闭实际的网络连接。仅设置内部标记，
        防止后续调用 _get_client() 继续使用。
        """
        self._redis = None
        self._closed = True
        logger.debug("Redis 时序发号器已标记为关闭")

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _build_key(game_id: str) -> str:
        """构建 Redis Key: werewolf:seq:{game_id}"""
        return RedisKeys.seq(game_id)

    async def _get_client(self) -> aioredis.Redis:
        """获取 Redis 客户端，通过共享连接池管理器。

        使用 :class:`RedisClientManager` 获取全局共享的 Redis 客户端，
        避免每个模块独立创建连接池导致连接数膨胀。

        Returns:
            共享的 :class:`redis.asyncio.Redis` 客户端实例。

        Raises:
            RedisUnavailableException: 发号器已关闭或 Redis 不可用。
        """
        if self._closed:
            raise RedisUnavailableException("Redis 时序发号器已关闭")

        if self._redis is None:
            try:
                self._redis = await RedisClientManager.get_client()
                logger.debug("Redis 时序发号器已获取共享客户端")
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                raise RedisUnavailableException(
                    "无法获取 Redis 客户端：共享连接池初始化失败"
                ) from e

        return self._redis


# ============================================================================
# 单例工厂
# ============================================================================

_redis_seq_instance: Optional[RedisSeqGenerator] = None
_instance_lock: Optional[asyncio.Lock] = None


async def get_redis_seq() -> RedisSeqGenerator:
    """获取 Redis 时序发号器的全局单例。

    首次调用时创建连接池并建立 Redis 连接，
    后续调用返回同一实例。

    Returns:
        全局唯一的 RedisSeqGenerator 实例
    """
    global _redis_seq_instance
    global _instance_lock

    if _redis_seq_instance is None:
        if _instance_lock is None:
            _instance_lock = asyncio.Lock()
            
        async with _instance_lock:
            # 双重检查锁定
            if _redis_seq_instance is None:
                _redis_seq_instance = RedisSeqGenerator()

    return _redis_seq_instance


async def reset_redis_seq() -> None:
    """重置 Redis 时序发号器单例 (仅用于测试)。"""
    global _redis_seq_instance
    global _instance_lock
    
    if _instance_lock is None:
        _instance_lock = asyncio.Lock()
        
    async with _instance_lock:
        if _redis_seq_instance is not None:
            await _redis_seq_instance.close()
            _redis_seq_instance = None
            logger.debug("Redis 时序发号器单例已重置")
