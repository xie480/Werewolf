# API 路由包初始化
from .games import router as games_router
from .players import router as players_router
from .actions import router as actions_router
from .events import router as events_router
from .models import router as models_router

__all__ = ["games_router", "players_router", "actions_router", "events_router", "models_router"]
