"""pytest 全局配置 — 初始化 structlog 日志系统。

**Why**: structlog 在未调用 `configure()` 时使用无输出的默认处理器，
导致所有日志静默丢弃。本文件在 pytest session 启动时调用 `setup_logger()`，
确保测试中的日志（包括 EventBus 的 `_default_log_subscriber`）能正常输出。
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path before importing ai_werewolf_core
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pytest
import pytest_asyncio
from ai_werewolf_core.utils.logger import setup_logger
from ai_werewolf_core.utils.redis_lua_loader import LuaScriptManager

# 模块加载时初始化 logger（pytest 在 collection 阶段加载 conftest.py，早于所有测试）
setup_logger()

@pytest_asyncio.fixture(autouse=True, scope="session")
async def load_lua_scripts():
    """在所有测试开始前加载 Lua 脚本。"""
    try:
        await LuaScriptManager.load_all_scripts()
    except Exception as e:
        import logging
        logging.warning(f"Failed to load lua scripts (Redis might not be running): {e}")
