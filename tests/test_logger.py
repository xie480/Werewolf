"""
日志系统异步并发测试脚本

验证 structlog 在 asyncio 并发环境下 contextvars 上下文隔离是否正常工作。
模拟多个 Agent 同时写入日志，确保 game_id / agent_id / phase 不会串号。

运行方式：
    cd f:/YilenaCode/Werewolf
    python -m pytest tests/test_logger.py -v
    或
    python tests/test_logger.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure the project root (Werewolf) is on sys.path so that
# `ai_werewolf_core` is importable from the tests/ directory.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from ai_werewolf_core.utils.logger import (
    setup_logger,
    get_logger,
    bind_game_context,
    bind_agent_context,
    clear_all_context,
    clear_agent_context,
    _inject_context,
)


def test_basic_logging():
    """测试基本日志输出（dev 模式带颜色）"""
    setup_logger()
    logger = get_logger("test_basic")

    bind_game_context("game_001", "NIGHT_WOLF_ACT")
    bind_agent_context("player_1")

    logger.info("狼人开始行动", event_type="werewolf_action", target="player_3")
    logger.warning("动作校验失败", event_type="action_validation_failed", retry=2)

    clear_all_context()
    logger.info("上下文已清除，此条日志不应包含 game_id/phase/agent_id")

    print("[PASS] test_basic_logging")


async def agent_task(agent_id: str, game_id: str, phase: str):
    """
    模拟单个 Agent 的执行任务。
    每个 Agent 在自己的 asyncio Task 中绑定独立的上下文并写入日志。
    """
    logger = get_logger(f"agent_{agent_id}")

    # 绑定当前 Agent 的对局上下文
    bind_game_context(game_id, phase)
    bind_agent_context(agent_id)

    # 模拟 Agent 执行过程中的多步日志
    logger.info("Agent 开始推理", event_type="agent_start")
    await asyncio.sleep(0.01)  # 模拟异步 I/O（如 LLM 调用）
    logger.info("Agent 推理完成", event_type="agent_finish", result="success")

    # 清除 Agent 上下文（phase 切换时调用）
    clear_agent_context()


import pytest

@pytest.mark.asyncio
async def test_concurrent_isolation():
    """
    核心测试：并发隔离验证。

    同时启动 5 个 Agent Task，每个绑定不同的 game_id 和 agent_id。
    通过自定义 structlog 配置 + LogCapture 捕获所有日志，
    验证每条日志的 game_id 和 agent_id 是否与发起者一致，无串号。
    """
    import structlog
    from structlog.testing import LogCapture

    # 使用包含 _inject_context 的自定义 processors 配置，最后捕获日志
    cap = LogCapture()
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            _inject_context,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            cap,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    tasks = [
        agent_task(f"player_{i}", f"game_{i % 2}", "NIGHT_WOLF_ACT")
        for i in range(5)
    ]

    await asyncio.gather(*tasks)

    captured = cap.entries

    # 验证每条日志的上下文是否正确
    errors = []
    for log in captured:
        agent_id = log.get("agent_id")
        game_id = log.get("game_id")
        phase = log.get("phase")

        if agent_id is None:
            errors.append(f"缺少 agent_id: {log}")
        if game_id is None:
            errors.append(f"缺少 game_id: {log}")
        if phase != "NIGHT_WOLF_ACT":
            errors.append(f"phase 不正确: {log}")

    if errors:
        print(f"\n[FAIL] test_concurrent_isolation - 发现 {len(errors)} 条异常日志：")
        for e in errors:
            print(f"  - {e}")
        raise AssertionError(f"并发隔离测试失败：{len(errors)} 条异常日志")
    else:
        print(f"[PASS] test_concurrent_isolation - 共 {len(captured)} 条日志，上下文全部正确")


async def main():
    """按顺序运行所有测试"""
    print("=" * 60)
    print("日志系统并发隔离测试")
    print("=" * 60)

    # 1. 基础功能测试
    print("\n>>> 测试 1: 基本日志输出")
    test_basic_logging()

    # 2. 并发隔离测试
    print("\n>>> 测试 2: asyncio 并发上下文隔离")
    await test_concurrent_isolation()

    print("\n" + "=" * 60)
    print("所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
