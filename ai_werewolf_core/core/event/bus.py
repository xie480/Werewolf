"""
事件总线 (Event Bus) 核心模块

负责接收、存储、路由和分发对局中的所有事件。
采用单例模式（或模块级实例）以确保全局唯一。

**ID 生成策略**:
    - 实体持久化 ID: 雪花算法 (Snowflake) — 替代 UUID v4
    - 事件时序 seq_num: Redis 原子递增 (INCR) — 替代内存自增
    详见 docs/agent.md
"""

import asyncio
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Awaitable, Any, Union

from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, Visibility

from ai_werewolf_core.utils.logger import get_logger
from ai_werewolf_core.utils.snowflake import get_snowflake
from ai_werewolf_core.utils.redis_seq import (
    RedisSeqGenerator,
    get_redis_seq,
    RedisUnavailableException,
)
from ai_werewolf_core.db.session import async_session_factory
from ai_werewolf_core.db.models import EventRecord

logger = get_logger(__name__)

# 订阅者回调函数类型：接收一个 Event 对象，返回 None 或 Awaitable
EventHandler = Callable[[Event], Union[None, Awaitable[None]]]

class EventBus:
    """
    事件总线核心类。

    全局实体 ID 使用雪花算法生成，seq_num 基于 Redis 原子递增，
    保证多进程部署下的全局唯一性和时序正确性。
    """

    def __init__(self, seq_generator: Optional[RedisSeqGenerator] = None):
        # 存储每个 game_id 的事件列表
        self._events: Dict[str, List[Event]] = defaultdict(list)

        # Redis 时序发号器 (支持依赖注入便于测试)
        self._seq_generator: Optional[RedisSeqGenerator] = seq_generator

        # 订阅者字典：EventType -> List[EventHandler]
        self._subscribers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        # 全局订阅者（订阅所有事件）
        self._global_subscribers: List[EventHandler] = []

        # 默认添加日志订阅者
        self.subscribe_all(self._default_log_subscriber)
        # 添加数据库持久化订阅者，实现事件溯源存储
        self.subscribe_all(self._persist_to_db)

    # ------------------------------------------------------------------
    # seq_num 管理
    # ------------------------------------------------------------------

    async def _ensure_seq_generator(self) -> RedisSeqGenerator:
        """懒初始化 Redis 时序发号器 (首次使用时建立连接)。"""
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
            RedisUnavailableException: Redis 不可用且重试耗尽
        """
        gen = await self._ensure_seq_generator()
        return await gen.next_seq(game_id)

    # ------------------------------------------------------------------
    # 事件发布
    # ------------------------------------------------------------------

    async def publish(self, event: Event) -> None:
        """
        发布事件。

        1. 分配 seq_num (通过 Redis 原子递增)
        2. 存入内存
        3. 异步分发给所有匹配的订阅者
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

        # 2. 存入内存
        self._events[event.game_id].append(event)

        # 3. 分发给订阅者
        handlers = self._global_subscribers + self._subscribers.get(event.event_type, [])

        for handler in handlers:
            try:
                res = handler(event)
                # 异步处理
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(
                    "事件处理函数执行异常",
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    error=str(e),
                    exc_info=True
                )

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """订阅特定类型的事件"""
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """订阅所有事件"""
        self._global_subscribers.append(handler)

    # ------------------------------------------------------------------
    # 事件查询
    # ------------------------------------------------------------------

    def get_events(self, game_id: str, agent_id: str) -> List[Event]:
        """
        为 Agent 提供按权限拉取历史事件的接口（内置可见性过滤）。

        根据统一的设计原则，`target_agents` 始终存储具体玩家 ID。
        PUBLIC 事件对所有 Agent 可见，PRIVATE 和 FACTION 事件仅对
        `target_agents` 列表中的 Agent 可见。

        Args:
            game_id: 对局 ID
            agent_id: 请求拉取事件的 Agent ID

        Returns:
            过滤后的事件列表
        """
        all_events = self._events.get(game_id, [])
        filtered_events: List[Event] = []

        for event in all_events:
            if event.visibility == Visibility.PUBLIC:
                filtered_events.append(event)
            elif event.visibility in (Visibility.PRIVATE, Visibility.FACTION):
                if agent_id in event.target_agents:
                    filtered_events.append(event)

        return filtered_events

    # ------------------------------------------------------------------
    # 内部订阅者
    # ------------------------------------------------------------------

    def _default_log_subscriber(self, event: Event) -> None:
        """默认的日志订阅者，将事件通过 structlog 输出"""
        logger.info(
            "EventBus 收到事件",
            event_id=event.event_id,
            game_id=event.game_id,
            seq_num=event.seq_num,
            event_type=event.event_type,
            visibility=event.visibility,
            target_agents=event.target_agents,
            payload=event.payload
        )

    async def _persist_to_db(self, event: Event) -> None:
        """
        数据库持久化订阅者 —— 将事件写入 EventRecord 表。

        **Why**: 遵循 Event Sourcing 架构，所有事件必须持久化到 PostgreSQL，
        以支持复盘回放和状态重建。此方法作为全局订阅者被 EventBus 自动调用。

        **ID 策略**: 使用雪花算法生成 EventRecord 的主键 ID，
        替代原有的 UUID v4，改善 B-Tree 索引写入性能。
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
            # NOTE: 持久化失败不应阻塞事件分发，仅记录错误。

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def clear(self, game_id: Optional[str] = None) -> None:
        """
        清理内存中的事件（用于测试或对局结束）。

        注意: seq_num 计数器由 Redis 管理，不受此方法影响。
        """
        if game_id:
            self._events.pop(game_id, None)
        else:
            self._events.clear()


# 全局单例
event_bus = EventBus()
