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
from typing import Dict, Optional, Set

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

    支持视角隔离（POV/上帝视角）：在 POV 模式下，系统会自动剔除其他玩家
    事件中的 inner_thought 字段，防止玩家通过抓包作弊。

    Attributes:
        _connections: ``game_id → {WebSocket: player_id}`` 的连接池映射。
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Dict[WebSocket, Optional[str]]] = defaultdict(dict)

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def connect(self, game_id: str, websocket: WebSocket, player_id: Optional[str] = None) -> None:
        """接受客户端连接并注册到连接池。

        Args:
            game_id: 客户端关注的对局 ID。
            websocket: FastAPI WebSocket 连接实例。
            player_id: 客户端绑定的玩家 ID（用于视角隔离，None 表示上帝视角/GOD模式）。
        """
        await websocket.accept()
        self._connections[game_id][websocket] = player_id
        logger.info(
            "ws_client_connected",
            game_id=game_id,
            player_id=player_id,
            total_connections=len(self._connections[game_id]),
        )

    async def disconnect(self, game_id: str, websocket: WebSocket) -> None:
        """从连接池中移除已断开的客户端。

        Args:
            game_id: 对局 ID。
            websocket: 已断开的 WebSocket 连接。
        """
        self._connections[game_id].pop(websocket, None)
        if not self._connections[game_id]:
            del self._connections[game_id]
        logger.info(
            "ws_client_disconnected",
            game_id=game_id,
            remaining=len(self._connections.get(game_id, {})),
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
        
        visibility = message.get("visibility")
        target_agents = message.get("target_agents", [])

        for ws, player_id in list(self._connections[game_id].items()):
            if ws is exclude:
                continue

            # 视角隔离 1: PRIVATE/FACTION 事件过滤
            if visibility and visibility != Visibility.PUBLIC.value:
                if player_id is not None and player_id not in target_agents:
                    continue  # POV 模式下，非目标玩家不可见私有事件

            # 视角隔离 2：如果是 POV 视角（player_id != None），且事件包含 inner_thought，
            # 且不是自己发出的动作，则剔除 inner_thought
            ws_message = message
            if player_id is not None and "payload" in message:
                payload = message["payload"]
                if isinstance(payload, dict) and "inner_thought" in payload:
                    actor = payload.get("actor_id") or payload.get("actor")
                    if actor != player_id:
                        # 复制一份 message 以免影响其他连接
                        ws_message = dict(message)
                        ws_message["payload"] = dict(payload)
                        del ws_message["payload"]["inner_thought"]

            ws_message_json = (
                json.dumps(ws_message, ensure_ascii=False)
                if ws_message is not message
                else message_json
            )

            try:
                await ws.send_text(ws_message_json)
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
            self._connections[game_id].pop(ws, None)

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

        推送所有事件到对应 game_id 的连接，由 broadcast_to_game 进行视角隔离。

        Args:
            event: EventBus 发布的新事件。
        """
        payload = {
            "type": "event",
            "event_id": event.event_id,
            "game_id": event.game_id,
            "seq_num": event.seq_num,
            "event_type": event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type),
            "visibility": event.visibility.value if hasattr(event.visibility, 'value') else str(event.visibility),
            "target_agents": event.target_agents,
            "timestamp": event.timestamp.isoformat(),
            "payload": event.payload,
        }
        await self.broadcast_to_game(event.game_id, payload)


# 全局单例
connection_manager = ConnectionManager()
