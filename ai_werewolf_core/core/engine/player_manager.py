"""
玩家状态缓存管理器 (PlayerStatusManager) —— 基于 Redis Hash + BitMap。

**Why**: 狼人杀对局中需要频繁校验玩家存活状态（is_alive）和身份（Role）。
在多 Worker 无状态架构下，每次查 DB 会导致数据库不堪重负。本模块将玩家
元数据和存活状态缓存到 Redis 中，提供 O(1) 复杂度的查询能力。

**数据分布**:
    - Hash: ``werewolf:players:{game_id}``
        Field: ``player_id``
        Value: JSON 字符串 ``{"role": "SEER", "seat": 3, "faction": "VILLAGER"}``

    - BitMap: ``werewolf:alive:{game_id}``
        Offset: ``seat_number``
        Bit: 1 = alive, 0 = dead

**为什么用 seat_number 作为 BitMap offset 而非 player_id**:
    seat_number 是紧凑整数（1-12），适合 BitMap 的 offset 语义；
    player_id 是字符串（如 "player_1"），无法直接用作 offset。

**TTL 策略**:
    对局结束后设置 1 小时 TTL，允许短暂保留用于复盘查询。

**一致性与容错**:
    - 死亡时同步: 先 SETBIT → 成功后再异步更新 DB PlayerRecord（最终一致性）
    - DB 为 Source of Truth: BitMap 不可用时，降级查询 DB
    - 初始化时批量写入: 使用 Pipeline 减少网络往返

参考:
    - :doc:`docs/plan/Redis缓存架构优化方案`
    - :doc:`docs/agent.md`
"""

import asyncio
import json
from typing import Dict, List, Optional, Set

import redis.asyncio as aioredis
from sqlalchemy import update

from ai_werewolf_core.config import settings
from ai_werewolf_core.constant.redis_keys import RedisKeys
from ai_werewolf_core.db.models import PlayerRecord
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.schemas.enums import Faction, Role
from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.utils.redis_seq import RedisUnavailableException
from ai_werewolf_core.utils.snowflake import get_snowflake

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 玩家数据 TTL (秒) —— 对局结束后 1 小时
PLAYER_DATA_TTL_SEC: int = 3600

# Redis 操作重试配置
RETRY_COUNT: int = 3
RETRY_DELAY_SEC: float = 0.1

# BitMap offset 上限 (预留 20 个座位)
MAX_SEAT_NUMBER: int = 20


# ============================================================================
# 玩家状态缓存管理器
# ============================================================================

class PlayerStatusManager:
    """玩家状态缓存管理器。

    统一管理玩家身份元数据和存活状态，基于 Redis Hash + BitMap。
    提供 O(1) 的存活校验，支持批量操作和 DB 穿透降级。

    Attributes:
        _redis: Redis 异步客户端（懒初始化，共享连接池）。
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None

    # ------------------------------------------------------------------
    # Redis 客户端懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 异步客户端（共享连接池）。

        Returns:
            共享的 Redis 异步客户端实例。

        Raises:
            RedisUnavailableException: Redis 连接池初始化失败。
        """
        try:
            return await RedisClientManager.get_client()
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                "PlayerStatusManager 无法获取 Redis 客户端"
            ) from e

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    async def init_players(
        self,
        game_id: str,
        players: Dict[str, dict],
    ) -> None:
        """批量初始化玩家数据到 Redis 并同步写入 PostgreSQL（Write-Through 模式）。

        使用 Pipeline 批量写入 Redis，减少网络往返次数。
        同时设置 TTL 防止数据永久残留。
        Redis 写入成功后，批量 INSERT PlayerRecord 行到 PostgreSQL。

        每局游戏只应调用一次，通常在 LifecycleManager.start_game() 中触发。

        **Write-Through 策略**:
            1. 先写 Redis（缓存层优先，保证热数据可用）
            2. 再写 PostgreSQL（持久层，作为 Source of Truth）
            3. DB 写入失败不阻塞主流程，仅记录 ERROR 日志

        Args:
            game_id: 对局唯一标识。
            players: ``player_id → player_info`` 的映射。
                player_info 格式: ``{"role": "SEER", "seat": 3, "faction": "VILLAGER"}``

        Raises:
            RedisUnavailableException: Redis 不可用。
            ValueError: players 为空或包含非法数据。
        """
        if not players:
            raise ValueError("players 不能为空")

        redis = await self._get_redis()
        info_key = RedisKeys.player_info(game_id)
        alive_key = RedisKeys.alive_bitmap(game_id)

        try:
            # 使用 Pipeline 批量写入 Redis
            async with redis.pipeline() as pipe:
                # 写入身份 Hash
                for player_id, info in players.items():
                    self._validate_player_info(player_id, info)
                    pipe.hset(info_key, player_id, json.dumps(info))

                    # 写入存活 BitMap: seat_number → 1 (存活)
                    seat = info["seat"]
                    if not (1 <= seat <= MAX_SEAT_NUMBER):
                        raise ValueError(
                            f"seat_number 必须在 1-{MAX_SEAT_NUMBER} 之间，实际: {seat}"
                        )
                    pipe.setbit(alive_key, seat, 1)

                # 设置 TTL
                pipe.expire(info_key, PLAYER_DATA_TTL_SEC)
                pipe.expire(alive_key, PLAYER_DATA_TTL_SEC)

                await pipe.execute()

            logger.info(
                "players_initialized_redis",
                game_id=game_id,
                player_count=len(players),
                seats=[p["seat"] for p in players.values()],
            )

        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"无法初始化玩家数据: game_id={game_id}"
            ) from e
        except aioredis.ResponseError as e:
            raise RedisUnavailableException(
                f"Redis 返回错误响应: {e}"
            ) from e

        # Write-Through: 批量 INSERT PlayerRecord 到 PostgreSQL
        await self._init_players_to_db(game_id, players)

    async def _init_players_to_db(
        self, game_id: str, players: Dict[str, dict]
    ) -> None:
        """Write-Through: 将玩家数据批量写入 PostgreSQL PlayerRecord 表。

        采用最终一致性策略：DB 写入失败不阻塞 Redis 已成功的数据，
        仅记录 ERROR 日志。每条 PlayerRecord 使用独立的 Snowflake ID。

        Args:
            game_id: 对局唯一标识。
            players: player_id → player_info 映射（已在 init_players 中校验过）。
        """
        snowflake = get_snowflake()
        try:
            async with async_session_factory() as session:
                for player_id, info in players.items():
                    # 将字符串角色名转换为 Role 枚举
                    role_str = info.get("role", "UNKNOWN")
                    try:
                        role = Role(role_str)
                    except ValueError:
                        logger.warning(
                            "无法识别的角色名，使用 UNKNOWN 降级",
                            player_id=player_id,
                            role_str=role_str,
                        )
                        role = Role.VILLAGER  # 安全降级为村民

                    record = PlayerRecord(
                        id=snowflake.next_id(),
                        game_id=game_id,
                        player_id=player_id,
                        seat_number=info["seat"],
                        role=role,
                        is_alive=True,
                        ai_profile_id=info.get("ai_profile_id"),
                    )
                    session.add(record)

                await session.commit()
                logger.info(
                    "players_initialized_db",
                    game_id=game_id,
                    player_count=len(players),
                )
        except Exception as e:
            # DB 写入失败不阻塞主流程——Redis 中的数据仍然有效
            logger.error(
                "DB 玩家数据初始化失败（Redis 已更新，存在短暂不一致）",
                game_id=game_id,
                player_count=len(players),
                error=str(e),
                exc_info=True,
            )

    @staticmethod
    def _validate_player_info(player_id: str, info: dict) -> None:
        """校验玩家信息格式。

        Args:
            player_id: 玩家 ID。
            info: 玩家信息字典。

        Raises:
            ValueError: 信息格式不合法。
        """
        required_fields = {"role", "seat", "faction"}
        missing = required_fields - set(info.keys())
        if missing:
            raise ValueError(
                f"玩家 [{player_id}] 信息缺少必要字段: {missing}"
            )
        if not isinstance(info["seat"], int):
            raise ValueError(
                f"玩家 [{player_id}] seat 必须为整数，实际: {type(info['seat'])}"
            )

    # ------------------------------------------------------------------
    # 存活状态查询与更新
    # ------------------------------------------------------------------

    async def is_alive(self, game_id: str, seat_number: int) -> bool:
        """GETBIT 查询玩家存活状态，O(1) 复杂度。

        **Why GETBIT**: BitMap 的 GETBIT 操作是 O(1) 时间复杂度，
        比 Hash 的 HGET 更高效，适合高频调用。

        Redis 不可用时降级查询 DB PlayerRecord。

        Args:
            game_id: 对局唯一标识。
            seat_number: 玩家座位号 (1-based)。

        Returns:
            ``True`` 如果玩家存活，``False`` 如果已死亡或不存在。

        Raises:
            ValueError: seat_number 不在有效范围内。
        """
        if not (1 <= seat_number <= MAX_SEAT_NUMBER):
            raise ValueError(
                f"seat_number 必须在 1-{MAX_SEAT_NUMBER} 之间，实际: {seat_number}"
            )

        alive_key = RedisKeys.alive_bitmap(game_id)

        try:
            redis = await self._get_redis()
            result = await redis.getbit(alive_key, seat_number)
            return result == 1
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            # 降级：查询 DB
            logger.warning(
                "Redis GETBIT 失败，降级查询 DB",
                game_id=game_id,
                seat_number=seat_number,
                error=str(e),
            )
            return await self._is_alive_from_db(game_id, seat_number)
        except aioredis.ResponseError as e:
            logger.error(
                "Redis GETBIT 响应异常",
                game_id=game_id,
                seat_number=seat_number,
                error=str(e),
            )
            return await self._is_alive_from_db(game_id, seat_number)

    async def _is_alive_from_db(self, game_id: str, seat_number: int) -> bool:
        """从 DB PlayerRecord 查询存活状态（降级路径）。

        Args:
            game_id: 对局 ID。
            seat_number: 座位号。

        Returns:
            ``True`` 如果存活，``False`` 如果不存在或已死亡。
        """
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                stmt = select(PlayerRecord.is_alive).where(
                    PlayerRecord.game_id == game_id,
                    PlayerRecord.seat_number == seat_number,
                )
                result = await session.execute(stmt)
                is_alive = result.scalar()
                return is_alive if is_alive is not None else False
        except Exception as e:
            logger.error(
                "DB 存活查询失败",
                game_id=game_id,
                seat_number=seat_number,
                error=str(e),
                exc_info=True,
            )
            return False

    async def mark_dead(
        self, game_id: str, player_id: str, seat_number: int
    ) -> None:
        """SETBIT 标记玩家死亡 + 异步更新 DB PlayerRecord。

        执行顺序：
        1. 更新 Redis BitMap (SETBIT 0)
        2. 异步更新 DB PlayerRecord (最终一致性)

        **Why (先 Redis 后 DB)**: Redis 是热数据缓存，DB 是 Source of Truth。
        即使 DB 更新失败，Redis 的正确状态可以保证当前对局的正常运行；
        DB 更新失败时记录 ERROR 日志，后续可通过对账修复。

        Args:
            game_id: 对局唯一标识。
            player_id: 玩家 ID。
            seat_number: 玩家座位号。

        Raises:
            RedisUnavailableException: Redis 操作失败。
            ValueError: seat_number 无效。
        """
        if not (1 <= seat_number <= MAX_SEAT_NUMBER):
            raise ValueError(
                f"seat_number 必须在 1-{MAX_SEAT_NUMBER} 之间，实际: {seat_number}"
            )

        alive_key = RedisKeys.alive_bitmap(game_id)
        redis = await self._get_redis()

        # Step 1: 更新 Redis BitMap
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                await redis.setbit(alive_key, seat_number, 0)
                logger.info(
                    "player_marked_dead_redis",
                    game_id=game_id,
                    player_id=player_id,
                    seat_number=seat_number,
                )
                break
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                logger.warning(
                    "Redis SETBIT 连接异常，重试中",
                    game_id=game_id,
                    player_id=player_id,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
                else:
                    raise RedisUnavailableException(
                        f"标记死亡失败: game_id={game_id}, player={player_id}"
                    ) from e

        # Step 2: 异步更新 DB (最终一致性，失败不阻塞)
        try:
            async with async_session_factory() as session:
                stmt = (
                    update(PlayerRecord)
                    .where(
                        PlayerRecord.game_id == game_id,
                        PlayerRecord.player_id == player_id,
                    )
                    .values(is_alive=False)
                )
                await session.execute(stmt)
                await session.commit()
                logger.debug(
                    "player_marked_dead_db",
                    game_id=game_id,
                    player_id=player_id,
                )
        except Exception as e:
            logger.error(
                "DB 死亡状态更新失败（将在后续对账中修复）",
                game_id=game_id,
                player_id=player_id,
                error=str(e),
                exc_info=True,
            )

    async def mark_alive(
        self, game_id: str, player_id: str, seat_number: int
    ) -> None:
        """SETBIT 标记玩家复活并更新 DB。

        **Why**: 女巫解药可以复活被狼杀的玩家。此方法用于复原 BitMap 状态。
        虽然不常见，但为了完整性提供此接口。

        Args:
            game_id: 对局唯一标识。
            player_id: 玩家 ID。
            seat_number: 玩家座位号。
        """
        if not (1 <= seat_number <= MAX_SEAT_NUMBER):
            raise ValueError(
                f"seat_number 必须在 1-{MAX_SEAT_NUMBER} 之间，实际: {seat_number}"
            )

        alive_key = RedisKeys.alive_bitmap(game_id)
        redis = await self._get_redis()

        try:
            await redis.setbit(alive_key, seat_number, 1)
            logger.info(
                "player_marked_alive",
                game_id=game_id,
                player_id=player_id,
                seat_number=seat_number,
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            raise RedisUnavailableException(
                f"标记复活失败: game_id={game_id}, player={player_id}"
            ) from e

        # 异步更新 DB
        try:
            async with async_session_factory() as session:
                stmt = (
                    update(PlayerRecord)
                    .where(
                        PlayerRecord.game_id == game_id,
                        PlayerRecord.player_id == player_id,
                    )
                    .values(is_alive=True)
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.error(
                "DB 复活状态更新失败",
                game_id=game_id,
                player_id=player_id,
                error=str(e),
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # 批量查询
    # ------------------------------------------------------------------

    async def get_alive_players(self, game_id: str) -> List[int]:
        """通过 BITFIELD 批量获取存活玩家的座位号列表。

        **Why BITFIELD**: 一次命令获取连续 N 个 bit 的值，
        比 N 次 GETBIT 减少网络往返。

        Args:
            game_id: 对局唯一标识。

        Returns:
            存活玩家的座位号列表（升序排列）。
        """
        alive_key = RedisKeys.alive_bitmap(game_id)

        try:
            redis = await self._get_redis()
            # BITFIELD 获取 1 到 MAX_SEAT_NUMBER 的所有位
            bf = redis.bitfield(alive_key)
            for i in range(1, MAX_SEAT_NUMBER + 1):
                bf.get("u1", f"#{i}")
            results = await bf.execute()
            
            alive_seats = [
                i + 1
                for i, bit in enumerate(results)
                if bit == 1
            ]
            return alive_seats
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            # 降级：查询 DB
            logger.warning(
                "Redis BITFIELD 失败，降级查询 DB",
                game_id=game_id,
                error=str(e),
            )
            return await self._get_alive_players_from_db(game_id)
        except aioredis.ResponseError as e:
            logger.error(
                "Redis BITFIELD 响应异常",
                game_id=game_id,
                error=str(e),
            )
            return await self._get_alive_players_from_db(game_id)

    async def _get_alive_players_from_db(self, game_id: str) -> List[int]:
        """从 DB 查询存活玩家（降级路径）。

        Args:
            game_id: 对局 ID。

        Returns:
            存活玩家的座位号列表。
        """
        try:
            async with async_session_factory() as session:
                from sqlalchemy import select
                stmt = select(PlayerRecord.seat_number).where(
                    PlayerRecord.game_id == game_id,
                    PlayerRecord.is_alive == True,
                )
                result = await session.execute(stmt)
                seats = [row[0] for row in result.all()]
                return sorted(seats)
        except Exception as e:
            logger.error(
                "DB 存活列表查询失败",
                game_id=game_id,
                error=str(e),
                exc_info=True,
            )
            return []

    async def get_dead_players(self, game_id: str) -> List[int]:
        """获取已死亡玩家的座位号列表。

        Args:
            game_id: 对局唯一标识。

        Returns:
            已死亡玩家的座位号列表（升序排列）。
        """
        alive_key = RedisKeys.alive_bitmap(game_id)

        try:
            redis = await self._get_redis()
            bf = redis.bitfield(alive_key)
            for i in range(1, MAX_SEAT_NUMBER + 1):
                bf.get("u1", f"#{i}")
            results = await bf.execute()
            
            dead_seats = [
                i + 1
                for i, bit in enumerate(results)
                if bit == 0
            ]
            return dead_seats
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "Redis BITFIELD (dead) 失败，降级查询 DB",
                game_id=game_id,
                error=str(e),
            )
            # 从存活列表反推死亡列表
            alive = await self._get_alive_players_from_db(game_id)
            all_seats = set(range(1, MAX_SEAT_NUMBER + 1))
            return sorted(all_seats - set(alive))
        except aioredis.ResponseError:
            alive = await self._get_alive_players_from_db(game_id)
            all_seats = set(range(1, MAX_SEAT_NUMBER + 1))
            return sorted(all_seats - set(alive))

    # ------------------------------------------------------------------
    # 身份信息查询
    # ------------------------------------------------------------------

    async def get_player_info(
        self, game_id: str, player_id: str
    ) -> Optional[dict]:
        """HGET 获取单个玩家的身份信息。

        Args:
            game_id: 对局唯一标识。
            player_id: 玩家 ID。

        Returns:
            玩家信息字典，如果不存在返回 None。
            格式: ``{"role": "SEER", "seat": 3, "faction": "VILLAGER"}``
        """
        info_key = RedisKeys.player_info(game_id)

        try:
            redis = await self._get_redis()
            raw = await redis.hget(info_key, player_id)
            if raw is None:
                return None
            return json.loads(raw)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "Redis HGET 失败，降级查询 DB",
                game_id=game_id,
                player_id=player_id,
                error=str(e),
            )
            return await self._get_player_info_from_db(game_id, player_id)
        except (json.JSONDecodeError, aioredis.ResponseError) as e:
            logger.error(
                "玩家信息反序列化失败",
                game_id=game_id,
                player_id=player_id,
                error=str(e),
            )
            return await self._get_player_info_from_db(game_id, player_id)

    async def _get_player_info_from_db(
        self, game_id: str, player_id: str
    ) -> Optional[dict]:
        """从 DB 查询玩家信息（降级路径）。

        Args:
            game_id: 对局 ID。
            player_id: 玩家 ID。

        Returns:
            玩家信息字典或 None。
        """
        try:
            # 创建数据库会话
            async with async_session_factory() as session:
                from sqlalchemy import select
                from ai_werewolf_core.db.models import AIPlayerProfile
                
                # 构建查询语句，查找指定游戏和玩家的记录
                stmt = select(PlayerRecord).where(
                    PlayerRecord.game_id == game_id,
                    PlayerRecord.player_id == player_id,
                )
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                # 如果没有找到记录则返回 None
                if record is None:
                    return None
                    
                # 默认模型 ID
                model_id = "deepseek-v4-flash"
                # 如果玩家有 AI 配置 ID，则查询对应的 model_id
                if record.ai_profile_id:
                    stmt2 = select(AIPlayerProfile.model_id).where(AIPlayerProfile.id == record.ai_profile_id)
                    res2 = await session.execute(stmt2)
                    profile_model_id = res2.scalar_one_or_none()
                    if profile_model_id:
                        model_id = profile_model_id
                        
                # 返回包含玩家详细信息的字典
                return {
                    "role": record.role.value if record.role else "UNKNOWN",
                    "seat": record.seat_number,
                    "faction": _infer_faction(record.role),
                    "ai_profile_id": record.ai_profile_id,
                    "model_id": model_id,
                }
        except Exception as e:
            logger.error(
                "DB 玩家信息查询失败",
                game_id=game_id,
                player_id=player_id,
                error=str(e),
                exc_info=True,
            )
            return None

    async def get_all_players(self, game_id: str) -> Dict[str, dict]:
        """HGETALL 获取全部玩家的身份信息。

        Args:
            game_id: 对局唯一标识。

        Returns:
            ``player_id → player_info`` 的映射。Redis 不可用时返回空字典。
        """
        info_key = RedisKeys.player_info(game_id)

        try:
            redis = await self._get_redis()
            raw = await redis.hgetall(info_key)
            if not raw:
                logger.warning("DIAGNOSIS_LOG: get_all_players Redis HGETALL returned empty for game_id=%s. Returning {} without DB fallback.", game_id)
                return {}
            return {
                player_id: json.loads(info_str)
                for player_id, info_str in raw.items()
            }
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "Redis HGETALL 失败",
                game_id=game_id,
                error=str(e),
            )
            return {}
        except (json.JSONDecodeError, aioredis.ResponseError) as e:
            logger.error(
                "批量玩家信息反序列化失败",
                game_id=game_id,
                error=str(e),
            )
            return {}

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def delete_game_data(self, game_id: str) -> None:
        """删除指定对局的所有玩家缓存数据。

        在对局彻底结束（FINISHED/ABORTED）后调用，清理 Redis 空间。
        注意：TTL 会自动过期，此方法用于主动清理。

        Args:
            game_id: 对局唯一标识。
        """
        info_key = RedisKeys.player_info(game_id)
        alive_key = RedisKeys.alive_bitmap(game_id)

        try:
            redis = await self._get_redis()
            await redis.delete(info_key, alive_key)
            logger.info(
                "player_cache_cleared",
                game_id=game_id,
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "清理玩家缓存失败（将由 TTL 自动过期）",
                game_id=game_id,
                error=str(e),
            )

    async def extend_ttl(self, game_id: str) -> None:
        """延长玩家数据的 TTL（对局仍在进行时周期性刷新）。

        **Why**: 长时间对局可能超过默认 TTL。在对局的关键阶段（如轮次推进时）
        调用此方法刷新过期时间，防止缓存提前过期。

        Args:
            game_id: 对局唯一标识。
        """
        info_key = RedisKeys.player_info(game_id)
        alive_key = RedisKeys.alive_bitmap(game_id)

        try:
            redis = await self._get_redis()
            await redis.expire(info_key, PLAYER_DATA_TTL_SEC)
            await redis.expire(alive_key, PLAYER_DATA_TTL_SEC)
            logger.debug("player_ttl_extended", game_id=game_id)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "延长 TTL 失败",
                game_id=game_id,
                error=str(e),
            )


# ============================================================================
# 工具函数
# ============================================================================

def _infer_faction(role: Optional[Role]) -> str:
    """根据角色推导阵营。

    **Why**: 简化参数传递，调用方不需要额外传递 faction。

    Args:
        role: 角色枚举。

    Returns:
        阵营字符串（"VILLAGER" 或 "WEREWOLF"）。
    """
    if role is None:
        return "UNKNOWN"
    if role == Role.WEREWOLF:
        return Faction.WEREWOLF.value
    return Faction.VILLAGER.value
