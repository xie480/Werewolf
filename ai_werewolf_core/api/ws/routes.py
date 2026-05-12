"""
WebSocket 路由端点 —— 客户端通过此端点订阅对局实时事件。

**Why**: 前端通过建立 WebSocket 连接接收对局的实时状态变更和事件推送，
无需轮询 REST API。每个连接绑定一个 game_id，仅接收该对局的 PUBLIC 事件。

用法（前端）:
    const ws = new WebSocket("ws://localhost:8000/ws/games/{game_id}")
    ws.onmessage = (event) => { const data = JSON.parse(event.data); ... }

参考 [`docs/system/Event System.md`](../../docs/system/Event%20System.md)。
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_werewolf_core.api.ws.manager import connection_manager
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/games/{game_id}")
async def game_events_ws(websocket: WebSocket, game_id: str):
    """WebSocket 端点 —— 订阅对局实时事件推送。

    建立连接后，客户端将持续接收该对局的 PUBLIC 事件推送。
    消息格式: JSON 对象，type="event"，包含完整事件信息。

    心跳机制: 服务端每 30 秒发送 ping 消息，客户端应回复 pong。
    也可以通过定期发送 {"type": "ping"} 来保持连接。

    Args:
        websocket: FastAPI WebSocket 连接。
        game_id: 要订阅的对局 ID。
    """
    await connection_manager.connect(game_id, websocket)

    try:
        # 发送欢迎消息确认连接建立
        await websocket.send_json({
            "type": "connected",
            "game_id": game_id,
            "message": f"已连接到对局 {game_id} 的实时事件推送",
        })

        # 保持连接，接收客户端消息（心跳等）
        while True:
            data = await websocket.receive_json()

            # 处理客户端 ping
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                logger.debug(
                    "ws_client_message",
                    game_id=game_id,
                    message_type=data.get("type", "unknown"),
                )

    except WebSocketDisconnect:
        logger.info("ws_client_disconnected_by_peer", game_id=game_id)
    except Exception as e:
        logger.error(
            "ws_unexpected_error",
            game_id=game_id,
            error=str(e),
            exc_info=True,
        )
    finally:
        await connection_manager.disconnect(game_id, websocket)
