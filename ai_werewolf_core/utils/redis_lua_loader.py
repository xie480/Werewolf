"""
Redis Lua 脚本管理器 —— 集中加载、注册与调用 Lua 脚本。

**Why**: 项目中多个模块（VoteManager、PhaseStateMachine、LifecycleManager）
存在"先读后写"或"多命令分步执行"的 Redis 操作，这些操作在多 Worker 部署下
存在竞态条件风险。使用 Lua 脚本可以保证这些复合操作的原子性。

本模块提供:
    1. 启动时自动扫描 ``redis_lua/`` 目录下所有 .lua 文件
    2. 通过 ``SCRIPT LOAD`` 将脚本注册到 Redis，缓存 SHA 摘要
    3. 通过 ``EVALSHA`` 调用脚本，自动在 ``NoScriptError`` 时回退 ``EVAL``

**设计模式**:
    单例模式（类级别状态），与 :class:`RedisClientManager` 风格一致。
    使用 ``asyncio.Lock`` 保护脚本加载过程的并发安全。

**使用示例**::

    from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

    # 启动时加载所有脚本
    await LuaScriptManager.load_all_scripts()

    # 调用脚本
    result = await LuaScriptManager.evalsha(
        "hset_with_ttl",
        keys=["werewolf:vote:game_001:1"],
        args=["player_1", "player_2", "86400"],
    )
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from redis.exceptions import NoScriptError

from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# Lua 脚本目录（相对于本模块所在包的根目录）
_LUA_SCRIPTS_DIR: str = str(
    Path(__file__).resolve().parent.parent / "redis_lua"
)

# 脚本加载重试配置
_LOAD_RETRY_COUNT: int = 3
_LOAD_RETRY_DELAY_SEC: float = 0.1


# ============================================================================
# 异常定义
# ============================================================================

class LuaScriptError(Exception):
    """Lua 脚本执行异常。

    当 Lua 脚本返回错误状态或脚本未找到时抛出。
    """

    def __init__(self, message: str, script_name: Optional[str] = None):
        self.script_name = script_name
        super().__init__(message)


class LuaScriptNotLoadedError(LuaScriptError):
    """Lua 脚本尚未加载异常。

    当调用方在 :meth:`LuaScriptManager.load_all_scripts` 之前尝试
    调用脚本时抛出。
    """

    def __init__(self, script_name: str, message: Optional[str] = None):
        if message is None:
            message = (
                f"Lua 脚本 [{script_name}] 尚未加载，"
                f"请先调用 load_all_scripts()"
            )
        super().__init__(message, script_name=script_name)


# ============================================================================
# Lua 脚本管理器
# ============================================================================

class LuaScriptManager:
    """Redis Lua 脚本管理器 —— 单例模式。

    管理所有 .lua 脚本的加载、注册和调用。脚本文件存放在
    ``ai_werewolf_core/redis_lua/`` 目录下，文件名（不含扩展名）
    即为脚本名称。

    线程安全:
        使用 :class:`asyncio.Lock` 保护脚本加载过程，
        支持多协程并发调用 :meth:`load_all_scripts`。

    Attributes:
        _scripts: 脚本名 → 脚本源码 的映射。
        _shas: 脚本名 → SHA1 摘要 的映射。
        _loaded: 标记是否已完成脚本加载。
        _lock: 异步锁，保护加载过程的并发安全。
    """

    _scripts: Dict[str, str] = {}
    """脚本名 → 脚本源码"""

    _shas: Dict[str, str] = {}
    """脚本名 → SHA1 摘要（由 SCRIPT LOAD 返回）"""

    _loaded: bool = False
    """标记是否已完成脚本加载"""

    _lock: Optional[asyncio.Lock] = None
    """异步锁，保护脚本加载的并发安全（懒初始化）"""

    # ------------------------------------------------------------------
    # 公开类方法
    # ------------------------------------------------------------------

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """获取或创建异步锁（懒初始化）。

        **Why**: 避免在模块导入时创建 asyncio.Lock ——
        此时可能没有运行中的事件循环，导致锁被绑定到隐式/默认事件循环，
        后续在 asyncio.run() / new_event_loop() 中使用时异常。

        Returns:
            已初始化的 :class:`asyncio.Lock` 实例。
        """
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def load_all_scripts(cls) -> None:
        """加载并注册所有 .lua 脚本到 Redis。

        扫描 ``redis_lua/`` 目录，读取所有 .lua 文件，
        通过 ``SCRIPT LOAD`` 命令注册到 Redis，缓存 SHA 摘要。

        幂等操作：重复调用不会重复加载。

        Raises:
            FileNotFoundError: 脚本目录不存在。
            redis.exceptions.ConnectionError: Redis 服务不可达。
            LuaScriptError: 脚本加载或注册失败。
        """
        if cls._loaded:
            return

        async with cls._get_lock():
            # 双重检查：锁内再次确认
            if cls._loaded:
                return

            # 1. 扫描目录，读取所有 .lua 文件
            if not os.path.isdir(_LUA_SCRIPTS_DIR):
                raise FileNotFoundError(
                    f"Lua 脚本目录不存在: {_LUA_SCRIPTS_DIR}"
                )

            lua_files = sorted(
                f for f in os.listdir(_LUA_SCRIPTS_DIR) if f.endswith(".lua")
            )
            if not lua_files:
                logger.warning(
                    "Lua 脚本目录为空，未加载任何脚本",
                    directory=_LUA_SCRIPTS_DIR,
                )
                cls._loaded = True
                return

            cls._scripts.clear()
            cls._shas.clear()

            for filename in lua_files:
                filepath = os.path.join(_LUA_SCRIPTS_DIR, filename)
                script_name = filename[:-4]  # 去掉 .lua 后缀

                with open(filepath, "r", encoding="utf-8") as f:
                    script_body = f.read()

                if not script_body.strip():
                    logger.warning(
                        "Lua 脚本文件为空，跳过",
                        script_name=script_name,
                        filepath=filepath,
                    )
                    continue

                cls._scripts[script_name] = script_body
                logger.debug(
                    "Lua 脚本已读取",
                    script_name=script_name,
                    filepath=filepath,
                    size_bytes=len(script_body),
                )

            # 2. 连接到 Redis 并注册脚本
            client = await RedisClientManager.get_client()
            for script_name, script_body in cls._scripts.items():
                sha = await cls._script_load_with_retry(
                    client, script_name, script_body
                )
                cls._shas[script_name] = sha

            cls._loaded = True
            logger.info(
                "所有 Lua 脚本加载完成",
                script_count=len(cls._scripts),
                script_names=list(cls._scripts.keys()),
            )

    @classmethod
    async def evalsha(
        cls,
        script_name: str,
        keys: Optional[List[str]] = None,
        args: Optional[List[str]] = None,
    ) -> Any:
        """通过 EVALSHA 调用已注册的 Lua 脚本。

        如果 EVALSHA 返回 ``NOSCRIPT`` 错误（脚本在 Redis 中丢失），
        自动回退到 EVAL 使用完整脚本源码重新执行。

        Args:
            script_name: 脚本名称（.lua 文件名不含扩展名）。
            keys: Redis Key 列表（对应 Lua 中的 KEYS）。
            args: 参数列表（对应 Lua 中的 ARGV）。

        Returns:
            脚本的返回值（类型取决于 Lua 脚本的 return 语句）。

        Raises:
            LuaScriptNotLoadedError: 脚本尚未加载。
            LuaScriptError: 脚本执行失败。
            redis.exceptions.ConnectionError: Redis 不可达。
        """
        if not cls._loaded:
            logger.warning(
                "Lua 脚本尚未加载，尝试自动加载",
                script_name=script_name,
            )
            await cls.load_all_scripts()

        sha = cls._shas.get(script_name)
        script_body = cls._scripts.get(script_name)
        if sha is None or script_body is None:
            # 自动加载后仍然没有该脚本，说明脚本文件缺失
            available = list(cls._scripts.keys())
            raise LuaScriptNotLoadedError(
                script_name,
                message=(
                    f"Lua 脚本 [{script_name}] 不存在或未成功加载。"
                    f" (已加载脚本: {available})"
                ),
            )

        keys = keys or []
        args = args or []

        client = await RedisClientManager.get_client()

        try:
            return await client.evalsha(sha, len(keys), *keys, *args)
        except NoScriptError:
            logger.warning(
                "EVALSHA 失败（NOSCRIPT），回退到 EVAL 执行",
                script_name=script_name,
            )
            # 重新注册脚本以更新 SHA 缓存
            new_sha = await cls._script_load_with_retry(
                client, script_name, script_body
            )
            cls._shas[script_name] = new_sha
            try:
                return await client.evalsha(new_sha, len(keys), *keys, *args)
            except NoScriptError:
                # 如果 EVALSHA 再次失败，直接用 EVAL
                return await client.eval(
                    script_body, len(keys), *keys, *args
                )
        except aioredis.ResponseError as e:
            raise LuaScriptError(
                f"Lua 脚本 [{script_name}] 执行失败: {e}",
                script_name=script_name,
            ) from e

    @classmethod
    async def reload_scripts(cls) -> None:
        """重新加载所有 Lua 脚本（仅用于开发/测试）。

        强制重新扫描目录并注册脚本，覆盖已有的 SHA 缓存。
        生产代码不应调用此方法。
        """
        async with cls._get_lock():
            cls._scripts.clear()
            cls._shas.clear()
            cls._loaded = False
            logger.debug("Lua 脚本管理器状态已重置")
        await cls.load_all_scripts()

    @classmethod
    async def reset(cls) -> None:
        """重置 Lua 脚本管理器状态（仅用于测试）。

        **Why**: 测试环境中需要在每个测试用例前后清理状态，
        确保测试隔离性。生产代码不应调用此方法。
        """
        async with cls._get_lock():
            cls._scripts.clear()
            cls._shas.clear()
            cls._loaded = False
            logger.debug("Lua 脚本管理器已重置")

    @classmethod
    def get_script_names(cls) -> List[str]:
        """获取已加载的脚本名称列表。

        Returns:
            脚本名称列表（按字母排序）。
        """
        return sorted(cls._scripts.keys())

    @classmethod
    def is_loaded(cls) -> bool:
        """检查脚本是否已完成加载。

        Returns:
            ``True`` 如果已加载所有脚本。
        """
        return cls._loaded

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @classmethod
    async def _script_load_with_retry(
        cls,
        client: aioredis.Redis,
        script_name: str,
        script_body: str,
    ) -> str:
        """通过 SCRIPT LOAD 注册脚本（带重试）。

        Args:
            client: Redis 客户端。
            script_name: 脚本名称（用于日志）。
            script_body: 脚本源码。

        Returns:
            脚本的 SHA1 摘要。

        Raises:
            LuaScriptError: 重试耗尽后仍注册失败。
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, _LOAD_RETRY_COUNT + 1):
            try:
                sha: str = await client.script_load(script_body)
                logger.debug(
                    "Lua 脚本已注册",
                    script_name=script_name,
                    sha=sha,
                )
                return sha
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                last_error = e
                logger.warning(
                    "SCRIPT LOAD 连接异常，重试中",
                    script_name=script_name,
                    attempt=attempt,
                    max_retries=_LOAD_RETRY_COUNT,
                    error=str(e),
                )
                if attempt < _LOAD_RETRY_COUNT:
                    await asyncio.sleep(_LOAD_RETRY_DELAY_SEC * attempt)
            except aioredis.ResponseError as e:
                raise LuaScriptError(
                    f"Lua 脚本 [{script_name}] SCRIPT LOAD 失败: {e}",
                    script_name=script_name,
                ) from e

        raise LuaScriptError(
            f"Lua 脚本 [{script_name}] SCRIPT LOAD 失败，"
            f"重试 {_LOAD_RETRY_COUNT} 次后 Redis 仍不可用",
            script_name=script_name,
        ) from last_error
