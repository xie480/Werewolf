"""
事件总线 (Event Bus) 核心模块

负责接收、存储、路由和分发对局中的所有事件。
采用单例模式（或模块级实例）以确保全局唯一。
"""

import asyncio
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Awaitable, Any, Union

from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.schemas.enums import EventType, Visibility
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

# 订阅者回调函数类型：接收一个 Event 对象，返回 None 或 Awaitable
EventHandler = Callable[[Event], Union[None, Awaitable[None]]]

class EventBus:
    """
    事件总线核心类。
    
    Phase 1: 使用内存存储事件和序列号。
    """
    
    def __init__(self):
        # 存储每个 game_id 的事件列表
        self._events: Dict[str, List[Event]] = defaultdict(list)
        # 存储每个 game_id 的当前最大 seq_num
        self._seq_nums: Dict[str, int] = defaultdict(int)
        
        # 订阅者字典：EventType -> List[EventHandler]
        self._subscribers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        # 全局订阅者（订阅所有事件）
        self._global_subscribers: List[EventHandler] = []
        
        # 默认添加日志订阅者
        self.subscribe_all(self._default_log_subscriber)

    def _generate_seq_num(self, game_id: str) -> int:
        """生成全局单调递增的序列号"""
        self._seq_nums[game_id] += 1
        return self._seq_nums[game_id]

    async def publish(self, event: Event) -> None:
        """
        发布事件。
        
        1. 分配 seq_num
        2. 存入内存
        3. 异步分发给所有匹配的订阅者
        """
        # 1. 分配 seq_num (如果尚未分配或为0)
        if event.seq_num <= 0:
            event.seq_num = self._generate_seq_num(event.game_id)
            
        # 2. 存入内存
        self._events[event.game_id].append(event)
        
        # 3. 分发给订阅者
        handlers = self._global_subscribers + self._subscribers.get(event.event_type, [])
        
        for handler in handlers:
            try:
                # 获取回调函数并注入参数
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

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """订阅特定类型的事件"""
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """订阅所有事件"""
        self._global_subscribers.append(handler)

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
        # 获取所有事件
        all_events = self._events.get(game_id, [])
        # 按可见性过滤
        filtered_events: List[Event] = []

        for event in all_events:
            if event.visibility == Visibility.PUBLIC:
                # 公开的就直接返回
                filtered_events.append(event)
            elif event.visibility in (Visibility.PRIVATE, Visibility.FACTION):
                # 私有的，则需要判断目标
                if agent_id in event.target_agents:
                    filtered_events.append(event)

        return filtered_events

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

    def clear(self, game_id: Optional[str] = None) -> None:
        """清理内存中的事件（用于测试或对局结束）"""
        if game_id:
            self._events.pop(game_id, None)
            self._seq_nums.pop(game_id, None)
        else:
            self._events.clear()
            self._seq_nums.clear()

# 全局单例
event_bus = EventBus()
