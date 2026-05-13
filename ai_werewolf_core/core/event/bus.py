"""
事件总线 (Event Bus) 核心模块 —— 基于 Redis Stream 的分布式事件存储。

负责接收、存储、路由和分发对局中的所有事件。
采用单例模式（模块级实例）以确保全局唯一。

**Redis Stream 设计**:
    - Key: werewolf:events:{game_id}
    - 消息 ID: {seq_num}-0 (使用 RedisSeqGenerator 生成的全局序号)
    - MAXLEN: ~1000 (近似裁剪，保留最近约 1000 条热数据)
    - 冷数据回退: Redis Stream 仅作为热数据缓存，全量历史数据穿透到
      PostgreSQL 的 EventRecord 表查询。

**ID 生成策略**:
    - 实体持久化 ID: 雪花算法 (Snowflake)
    - 事件时序 seq_num: Redis 原子递增 (INCR)
    详见 docs/agent.md

**降级策略**:
    当 Redis 不可用时，仍允许 EventBus 继续工作：
    - 事件发布：seq_num 分配失败时抛出异常；Stream 写入失败时记录
      CRITICAL 日志，但仍尝试持久化到 DB（_persist_to_db 不依赖内存）
    - 事件查询：回退到 PostgreSQL EventRecord 表全量查询
"""

import json
from typing import Callable, Optional, Awaitable, Union, List

import redis.asyncio as aioredis
from sqlalchemy import select, and_

from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, Visibility
from ai_werewolf_core.constant.redis_keys import RedisKeys

from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.snowflake import get_snowflake
from ai_werewolf_core.utils.redis_seq import (
    RedisSeqGenerator,
    get_redis_seq,
    RedisUnavailableException,
)
from ai_werewolf_core.utils.redis_client import RedisClientManager
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.db.models import EventRecord

logger = get_logger(__name__)

# 订阅者回调函数类型：接收一个 Event 对象，返回 None 或 Awaitable
EventHandler = Callable[[Event], Union[None, Awaitable[None]]]

# ============================================================================
# 常量定义
# ============================================================================

# Redis Stream 最大长度（近似值，实际可能超出约 10%）
STREAM_MAXLEN: int = 1000

# 热数据默认拉取数量
DEFAULT_XRANGE_COUNT: int = 100


class EventBus:
    """
    事件总线核心类 —— 基于 Redis Stream 的分布式事件管理。

    全局实体 ID 使用雪花算法生成，seq_num 基于 Redis 原子递增，
    事件热数据存储于 Redis Stream，冷数据穿透到 PostgreSQL，
    保证多进程部署下的全局唯一性和时序正确性。

    Attributes:
        _seq_generator: Redis 时序发号器（懒初始化）。
        _redis_client: Redis 异步客户端（通过共享连接池获取）。
        _subscribers: 按 EventType 分组的订阅者映射。
        _global_subscribers: 全局订阅者列表（订阅所有事件）。
    """

    def __init__(self, seq_generator: Optional[RedisSeqGenerator] = None):
        # Redis 时序发号器 (支持依赖注入便于测试)
        self._seq_generator: Optional[RedisSeqGenerator] = seq_generator

        # Redis 客户端引用 (懒初始化)
        self._redis_client: Optional[aioredis.Redis] = None

        # 订阅者字典：EventType -> List[EventHandler]
        from collections import defaultdict
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        # 全局订阅者（订阅所有事件）
        self._global_subscribers: list[EventHandler] = []

        # 默认添加日志订阅者
        self.subscribe_all(self._default_log_subscriber)
        # 添加数据库持久化订阅者，实现事件溯源存储
        self.subscribe_all(self._persist_to_db)

    # ------------------------------------------------------------------
    # Redis 客户端懒初始化
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """获取 Redis 异步客户端（懒初始化，共享连接池）。

        Returns:
            共享的 Redis 异步客户端实例。

        Raises:
            RedisUnavailableException: Redis 连接池初始化失败。
        """
        if self._redis_client is None:
            try:
                self._redis_client = await RedisClientManager.get_client()
                logger.debug("EventBus 已获取共享 Redis 客户端")
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                raise RedisUnavailableException(
                    "EventBus 无法获取 Redis 客户端"
                ) from e
        return self._redis_client

    # ------------------------------------------------------------------
    # seq_num 管理
    # ------------------------------------------------------------------

    async def _ensure_seq_generator(self) -> RedisSeqGenerator:
        """懒初始化 Redis 时序发号器 (首次使用时建立连接)。

        Returns:
            全局唯一的 RedisSeqGenerator 实例。
        """
        if self._seq_generator is None:
            self._seq_generator = await get_redis_seq()
        return self._seq_generator

    async def _generate_seq_num(self, game_id: str) -> int:
        """
        通过 Redis 原子递增生成全局唯一 seq_num。

        **Why Redis INCR**:
            - 单线程模型天然保证原子性，高并发无竞态
            - 持久化存储，进程重启后计数不丢失
            - 多进程共享同一计数器

        Raises:
            RedisUnavailableException: Redis 不可用且重试耗尽。
        """
        gen = await self._ensure_seq_generator()
        return await gen.next_seq(game_id)

    # ------------------------------------------------------------------
    # Redis Stream 操作
    # ------------------------------------------------------------------

    async def _xadd_event(self, event: Event) -> str:
        """将事件写入 Redis Stream。

        使用已分配的 seq_num 作为消息 ID（格式: {seq_num}-0），
        保证严格的全局时序。使用 MAXLEN ~ 1000 控制 Stream 大小，
        防止内存无限增长。

        **Why MAXLEN ~**: 使用近似裁剪（~），Redis 会在宏观上维持
        约 1000 条消息，而非精确裁剪。这避免了每次 XADD 都执行
        裁剪操作的性能开销。

        Args:
            event: 已分配 seq_num 的事件。

        Returns:
            Redis Stream 消息 ID。

        Raises:
            RedisUnavailableException: Stream 写入失败。
        """
        stream_key = RedisKeys.event_stream(event.game_id)
        message_id = f"{event.seq_num}-0"

        try:
            redis = await self._get_redis()
            result = await redis.xadd(
                stream_key,
                {
                    "event_id": event.event_id,
                    "game_id": event.game_id,
                    "seq_num": str(event.seq_num),
                    "event_type": event.event_type,
                    "visibility": event.visibility,
                    "target_agents": json.dumps(event.target_agents),
                    "timestamp": event.timestamp.isoformat(),
                    "payload": json.dumps(event.payload),
                },
                id=message_id,
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            logger.debug(
                "event_xadd_success",
                game_id=event.game_id,
                seq_num=event.seq_num,
                stream_key=stream_key,
                msg_id=result,
            )
            return result
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.critical(
                "Redis Stream 写入失败——事件热数据缓存丢失，但将继续持久化到 DB",
                event_id=event.event_id,
                game_id=event.game_id,
                seq_num=event.seq_num,
                error=str(e),
                exc_info=True,
            )
            raise RedisUnavailableException(
                f"无法将事件写入 Redis Stream: game_id={event.game_id}"
            ) from e
        except aioredis.ResponseError as e:
            logger.error(
                "Redis Stream 响应异常",
                event_id=event.event_id,
                game_id=event.game_id,
                error=str(e),
                exc_info=True,
            )
            raise RedisUnavailableException(
                f"Redis 返回错误响应: {e}"
            ) from e

    async def _xrange_events(
        self,
        game_id: str,
        start_seq: int = 0,
        count: int = DEFAULT_XRANGE_COUNT,
    ) -> list[Event]:
        """从 Redis Stream 拉取热数据事件。

        使用 XRANGE 按 seq_num 范围查询。Stream 消息 ID 格式为
        {seq_num}-0，所以通过设置 start 和 end 的 ID 来限定范围。

        Args:
            game_id: 对局 ID。
            start_seq: 起始 seq_num（0 表示从最早开始）。
            count: 最大拉取数量。

        Returns:
            反序列化后的 Event 对象列表。
        """
        stream_key = RedisKeys.event_stream(game_id)
        start_id = f"{start_seq}-0" if start_seq > 0 else "-"
        end_id = "+"

        try:
            redis = await self._get_redis()
            raw_messages = await redis.xrange(
                stream_key,
                min=start_id,
                max=end_id,
                count=count,
            )
            return self._deserialize_stream_messages(raw_messages)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "Redis Stream 读取失败，将回退到 DB 查询",
                game_id=game_id,
                error=str(e),
            )
            return []
        except aioredis.ResponseError as e:
            logger.error(
                "Redis Stream XRANGE 异常",
                game_id=game_id,
                error=str(e),
                exc_info=True,
            )
            return []

    async def _xread_events(
        self,
        game_id: str,
        last_seen_id: str = "0",
        count: int = DEFAULT_XRANGE_COUNT,
    ) -> list[Event]:
        """增量拉取新事件（用于 Agent 轮询）。

        使用 XREAD 阻塞读取自 last_seen_id 之后的新消息。

        Args:
            game_id: 对局 ID。
            last_seen_id: 最后看到的消息 ID（"0" 表示从头开始）。
            count: 最大拉取数量。

        Returns:
            新事件的列表（可能为空）。
        """
        stream_key = RedisKeys.event_stream(game_id)

        try:
            redis = await self._get_redis()
            result = await redis.xread(
                {stream_key: last_seen_id},
                count=count,
                block=100,  # 短暂阻塞 100ms，避免空轮询
            )
            if not result:
                return []

            # result 格式: [(stream_key, [(msg_id, fields), ...])]
            raw_messages = result[0][1]
            return self._deserialize_stream_messages(raw_messages)
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "Redis Stream XREAD 失败",
                game_id=game_id,
                error=str(e),
            )
            return []
        except aioredis.ResponseError as e:
            logger.error(
                "Redis Stream XREAD 异常",
                game_id=game_id,
                error=str(e),
                exc_info=True,
            )
            return []

    @staticmethod
    def _deserialize_stream_messages(
        raw_messages: list[tuple[str, dict]],
    ) -> list[Event]:
        """将 Redis Stream 原始消息反序列化为 Event 对象列表。

        Args:
            raw_messages: XRANGE/XREAD 返回的原始消息列表，
                格式为 [(msg_id, fields_dict), ...]。

        Returns:
            反序列化后的 Event 对象列表。
        """
        from datetime import datetime
        from ai_werewolf_core.utils.time_utils import now_tz

        events: list[Event] = []
        for msg_id, fields in raw_messages:
            try:
                event = Event(
                    event_id=fields.get("event_id", ""),
                    game_id=fields.get("game_id", ""),
                    seq_num=int(fields.get("seq_num", 0)),
                    event_type=EventType(fields.get("event_type", "")),
                    visibility=Visibility(fields.get("visibility", "")),
                    target_agents=json.loads(fields.get("target_agents", "[]")),
                    timestamp=datetime.fromisoformat(
                        fields.get("timestamp", now_tz().isoformat())
                    ),
                    payload=json.loads(fields.get("payload", "{}")),
                )
                events.append(event)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.error(
                    "事件反序列化失败，跳过该消息",
                    msg_id=msg_id,
                    error=str(e),
                )

        return events

    # ------------------------------------------------------------------
    # DB 冷数据查询（穿透回退）
    # ------------------------------------------------------------------

    async def _query_db_events(
        self,
        game_id: str,
        min_seq: int = 0,
        limit: int = DEFAULT_XRANGE_COUNT,
    ) -> list[Event]:
        """从 PostgreSQL EventRecord 表查询冷数据事件。

        **Why**: 当 Redis Stream 因 MAXLEN 裁剪或宕机导致数据不全时，
        穿透到 DB 查询全量历史事件。

        Args:
            game_id: 对局 ID。
            min_seq: 最小 seq_num（用于分页/补全）。
            limit: 最大返回数量。

        Returns:
            从 DB 查询到的 Event 对象列表。
        """
        try:
            async with async_session_factory() as session:
                stmt = (
                    select(EventRecord)
                    .where(
                        and_(
                            EventRecord.game_id == game_id,
                            EventRecord.seq_num >= min_seq,
                        )
                    )
                    .order_by(EventRecord.seq_num.asc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                records = result.scalars().all()

                events: list[Event] = []
                for record in records:
                    event = Event(
                        event_id=record.event_id,
                        game_id=record.game_id,
                        seq_num=record.seq_num,
                        event_type=record.event_type,
                        visibility=record.visibility,
                        target_agents=list(record.target_agents),
                        timestamp=record.timestamp,
                        payload=dict(record.payload),
                    )
                    events.append(event)

                logger.debug(
                    "db_events_query",
                    game_id=game_id,
                    min_seq=min_seq,
                    count=len(events),
                )
                return events
        except Exception as e:
            logger.error(
                "DB 事件查询失败",
                game_id=game_id,
                error=str(e),
                exc_info=True,
            )
            return []

    # ------------------------------------------------------------------
    # 事件发布
    # ------------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """
        发布事件。

        执行流程：
        1. 通过 Redis 原子递增分配 seq_num（如果尚未分配）
        2. 将事件写入 Redis Stream 热数据缓存
        3. 异步分发给所有匹配的订阅者（包括 DB 持久化）

        **降级策略**: 如果 Redis Stream 写入失败，仍继续分发事件
        （包括 DB 持久化），确保"事实不可丢"。仅记录 CRITICAL 日志。

        Args:
            event: 待发布的事件。

        Raises:
            RedisUnavailableException: seq_num 分配失败（Redis 不可用）。
        """
        # 1. 分配 seq_num (如果尚未分配或为0)
        if event.seq_num <= 0:
            try:
                event.seq_num = await self._generate_seq_num(event.game_id)
            except RedisUnavailableException as e:
                logger.error(
                    "Redis 不可用，无法分配 seq_num，事件发布失败",
                    event_id=event.event_id,
                    game_id=event.game_id,
                    error=str(e),
                    exc_info=True,
                )
                raise

        # 2. 写入 Redis Stream 热数据缓存
        # Why: Stream 写入失败不阻塞发布——热数据可丢，事实不可丢
        try:
            await self._xadd_event(event)
        except RedisUnavailableException:
            # 已在 _xadd_event 内部记录 CRITICAL 日志，此处静默吞下
            pass

        # 3. 分发给订阅者
        handlers = list(self._global_subscribers)
        handlers.extend(self._subscribers.get(event.event_type, []))

        for handler in handlers:
            try:
                res = handler(event)
                if hasattr(res, '__await__'):  # 更安全的协程检测
                    await res
            except Exception as e:
                logger.error(
                    "事件处理函数执行异常",
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    error=str(e),
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """订阅特定类型的事件。

        Args:
            event_type: 要订阅的事件类型。
            handler: 事件处理回调函数。
        """
        self._subscribers[event_type].append(handler)
        logger.debug(
            "event_subscribe",
            event_type=event_type.value,
            handler=handler.__name__ if hasattr(handler, '__name__') else str(handler),
        )

    def subscribe_all(self, handler: EventHandler) -> None:
        """订阅所有类型的事件（全局订阅者）。

        Args:
            handler: 事件处理回调函数。
        """
        self._global_subscribers.append(handler)
        logger.debug(
            "global_subscribe",
            handler=handler.__name__ if hasattr(handler, '__name__') else str(handler),
        )

    # ------------------------------------------------------------------
    # 事件查询
    # ------------------------------------------------------------------

    async def get_events(
        self,
        game_id: str,
        agent_id: str,
        start_seq: int = 0,
        count: int = DEFAULT_XRANGE_COUNT,
    ) -> list[Event]:
        """
        为 Agent 提供按权限拉取历史事件的接口（内置可见性过滤）。

        优先从 Redis Stream 热数据缓存拉取；如果缓存数据不足，
        则穿透到 PostgreSQL EventRecord 表查询冷数据。

        可见性过滤规则：
        - PUBLIC 事件对所有 Agent 可见。
        - PRIVATE 和 FACTION 事件仅对 target_agents 列表中的 Agent 可见。

        Args:
            game_id: 对局 ID。
            agent_id: 请求拉取事件的 Agent ID。
            start_seq: 起始 seq_num（0 表示从最早开始）。
            count: 最大拉取数量。

        Returns:
            过滤后的事件列表（按 seq_num 升序排列）。
        """
        # Step 1: 从 Redis Stream 拉取热数据
        stream_events = await self._xrange_events(game_id, start_seq, count)

        # Step 2: 如果 Redis 数据不足，穿透查询 DB
        if len(stream_events) < count:
            # 计算 DB 查询的起始 seq_num
            if stream_events:
                db_min_seq = stream_events[-1].seq_num + 1
            else:
                db_min_seq = start_seq

            db_events = await self._query_db_events(
                game_id,
                min_seq=db_min_seq,
                limit=count - len(stream_events),
            )
            # 合并并按 seq_num 排序
            all_events = stream_events + db_events
            all_events.sort(key=lambda e: e.seq_num)
        else:
            all_events = stream_events

        # Step 3: 按可见性过滤
        filtered_events: list[Event] = []
        for event in all_events:
            if event.visibility == Visibility.PUBLIC:
                filtered_events.append(event)
            elif event.visibility in (Visibility.PRIVATE, Visibility.FACTION):
                if agent_id in event.target_agents:
                    filtered_events.append(event)

        logger.debug(
            "events_queried",
            game_id=game_id,
            agent_id=agent_id,
            total_events=len(all_events),
            filtered_count=len(filtered_events),
            stream_count=len(stream_events),
        )

        return filtered_events

    async def get_event_count(self, game_id: str) -> int:
        """获取指定对局的事件总数。

        优先从 Redis Stream 获取，不可用时从 DB 查询。

        Args:
            game_id: 对局 ID。

        Returns:
            事件总数。
        """
        stream_key = RedisKeys.event_stream(game_id)
        try:
            redis = await self._get_redis()
            return await redis.xlen(stream_key)
        except (aioredis.ConnectionError, aioredis.TimeoutError):
            # 回退到 DB 查询
            try:
                async with async_session_factory() as session:
                    from sqlalchemy import func
                    stmt = select(func.count()).where(
                        EventRecord.game_id == game_id
                    )
                    result = await session.execute(stmt)
                    return result.scalar() or 0
            except Exception as e:
                logger.error(
                    "事件计数查询失败",
                    game_id=game_id,
                    error=str(e),
                )
                return 0

    # ------------------------------------------------------------------
    # 内部订阅者
    # ------------------------------------------------------------------

    def _default_log_subscriber(self, event: Event) -> None:
        """默认的日志订阅者，将事件通过 structlog 输出。

        **Why**: 所有事件都应被记录到结构化日志中，
        便于运维排查和离线审计。
        """
        logger.info(
            "EventBus 收到事件",
            event_id=event.event_id,
            game_id=event.game_id,
            seq_num=event.seq_num,
            event_type=event.event_type,
            visibility=event.visibility,
            target_agents=event.target_agents,
            payload_keys=list(event.payload.keys()) if event.payload else [],
        )

    async def _persist_to_db(self, event: Event) -> None:
        """
        数据库持久化订阅者 —— 将事件写入 EventRecord 表。

        **Why**: 遵循 Event Sourcing 架构，所有事件必须持久化到 PostgreSQL，
        以支持复盘回放和状态重建。此方法作为全局订阅者被 EventBus 自动调用。

        **ID 策略**: 使用雪花算法生成 EventRecord 的主键 ID，
        替代原有的 UUID v4，改善 B-Tree 索引写入性能。

        NOTE: 持久化失败不应阻塞事件分发，仅记录错误。
        """
        try:
            record = EventRecord(
                id=get_snowflake().next_id(),
                event_id=event.event_id,
                game_id=event.game_id,
                seq_num=event.seq_num,
                event_type=event.event_type,
                visibility=event.visibility,
                target_agents=event.target_agents,
                payload=event.payload,
                timestamp=event.timestamp,
            )
            async with async_session_factory() as session:
                session.add(record)
                await session.commit()
                logger.debug(
                    "事件已持久化",
                    event_id=event.event_id,
                    game_id=event.game_id,
                    seq_num=event.seq_num,
                )
        except Exception as e:
            logger.error(
                "事件持久化失败",
                event_id=event.event_id,
                game_id=event.game_id,
                error=str(e),
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def clear(self, game_id: Optional[str] = None) -> None:
        """
        清理事件数据。

        如果指定 game_id，删除该对局的 Redis Stream 中的事件；
        否则清理所有对局的事件。

        注意: seq_num 计数器由 Redis 管理，不受此方法影响。
        PostgreSQL 中的 EventRecord 也不受此方法影响（仅清理热数据缓存）。

        Args:
            game_id: 对局 ID，None 表示清理所有。
        """
        try:
            redis = await self._get_redis()
            if game_id:
                stream_key = RedisKeys.event_stream(game_id)
                await redis.delete(stream_key)
                logger.info("event_stream_cleared", game_id=game_id)
            else:
                # 清理所有对局的 Event Stream
                # 使用 SCAN 匹配模式，避免 KEYS 阻塞
                pattern = f"{RedisKeys.EVENT_STREAM_PREFIX}:*"
                cursor = 0
                deleted_count = 0
                while True:
                    cursor, keys = await redis.scan(
                        cursor, match=pattern, count=100
                    )
                    if keys:
                        await redis.delete(*keys)
                        deleted_count += len(keys)
                    if cursor == 0:
                        break
                logger.info(
                    "all_event_streams_cleared",
                    deleted_count=deleted_count,
                )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.warning(
                "清理事件 Stream 时 Redis 不可用",
                game_id=game_id,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                "清理事件 Stream 异常",
                game_id=game_id,
                error=str(e),
                exc_info=True,
            )


# 全局单例
event_bus = EventBus()
