"""
WebSocket 连接管理器 —— 管理客户端连接并实时推送事件。

**Why**: 前端需要实时接收对局状态变更和事件通知。
本模块订阅 EventBus 的全局事件，每当新事件发布时，
自动推送给已连接的 WebSocket 客户端（按 game_id 路由）。

连接生命周期:
    1. 客户端通过 WebSocket 连接并携带 game_id
    2. 连接管理器将其注册到对应 game_id 的连接池
    3. EventBus 发布新事件时，推送给该 game_id 的所有连接
    4. 客户端断开时从连接池移除

参考 [`docs/system/Event System.md`](../../docs/system/Event%20System.md)。
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Dict, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from ai_werewolf_core.schemas.enums import EventType, Visibility
from ai_werewolf_core.schemas.models import Event
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器 —— 单例模式。

    管理所有活跃的 WebSocket 连接，按 game_id 分组路由事件推送。
    作为 EventBus 的全局订阅者，自动收到所有新事件并推送给相关客户端。

    Attributes:
        _connections: ``game_id → set[WebSocket]`` 的连接池映射。
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def connect(self, game_id: str, websocket: WebSocket) -> None:
        """接受客户端连接并注册到连接池。

        Args:
            game_id: 客户端关注的对局 ID。
            websocket: FastAPI WebSocket 连接实例。
        """
        await websocket.accept()
        self._connections[game_id].add(websocket)
        logger.info(
            "ws_client_connected",
            game_id=game_id,
            total_connections=len(self._connections[game_id]),
        )

    async def disconnect(self, game_id: str, websocket: WebSocket) -> None:
        """从连接池中移除已断开的客户端。

        Args:
            game_id: 对局 ID。
            websocket: 已断开的 WebSocket 连接。
        """
        self._connections[game_id].discard(websocket)
        if not self._connections[game_id]:
            del self._connections[game_id]
        logger.info(
            "ws_client_disconnected",
            game_id=game_id,
            remaining=len(self._connections.get(game_id, set())),
        )

    # ------------------------------------------------------------------
    # 消息推送
    # ------------------------------------------------------------------

    async def broadcast_to_game(
        self, game_id: str, message: dict, exclude: WebSocket | None = None
    ) -> None:
        """向指定对局的所有连接推送消息。

        连接断开时会自动清理，不影响其他连接。

        Args:
            game_id: 目标对局 ID。
            message: 要推送的消息字典，将序列化为 JSON。
            exclude: 可选的排除连接（如消息发送者自身）。
        """
        if game_id not in self._connections:
            return

        dead: list[WebSocket] = []
        message_json = json.dumps(message, ensure_ascii=False)

        for ws in self._connections[game_id]:
            if ws is exclude:
                continue
            try:
                await ws.send_text(message_json)
            except (WebSocketDisconnect, RuntimeError):
                dead.append(ws)
            except Exception as e:
                logger.warning(
                    "ws_send_failed",
                    game_id=game_id,
                    error=str(e),
                )
                dead.append(ws)

        # 清理已断开的连接
        for ws in dead:
            self._connections[game_id].discard(ws)

    async def broadcast_all(self, message: dict) -> None:
        """向所有连接推送广播消息（如系统公告）。

        Args:
            message: 要推送的消息字典。
        """
        for game_id in list(self._connections.keys()):
            await self.broadcast_to_game(game_id, message)

    # ------------------------------------------------------------------
    # EventBus 订阅回调 —— 自动推送事件到 WebSocket 客户端
    # ------------------------------------------------------------------

    async def on_event(self, event: Event) -> None:
        """EventBus 事件回调 —— 将新事件推送给相关 WebSocket 客户端。

        仅推送 PUBLIC 可见性的事件到对应 game_id 的所有连接。
        PRIVATE 和 FACTION 事件不在 WebSocket 层面处理（由 Agent 拉取）。

        Args:
            event: EventBus 发布的新事件。
        """
        # 仅推送 PUBLIC 事件——私有事件由 Agent 自行轮询拉取
        if event.visibility != Visibility.PUBLIC:
            return

        payload = {
            "type": "event",
            "event_id": event.event_id,
            "game_id": event.game_id,
            "seq_num": event.seq_num,
            "event_type": event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
            "timestamp": event.timestamp.isoformat(),
            "payload": event.payload,
        }
        await self.broadcast_to_game(event.game_id, payload)


# 全局单例
connection_manager = ConnectionManager()
