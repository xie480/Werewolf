"""pytest 全局配置 — 初始化 structlog 日志系统。

**Why**: structlog 在未调用 `configure()` 时使用无输出的默认处理器，
导致所有日志静默丢弃。本文件在 pytest session 启动时调用 `setup_logger()`，
确保测试中的日志（包括 EventBus 的 `_default_log_subscriber`）能正常输出。
"""

from ai_werewolf_core.utils.logger import setup_logger

# 模块加载时初始化 logger（pytest 在 collection 阶段加载 conftest.py，早于所有测试）
setup_logger()
