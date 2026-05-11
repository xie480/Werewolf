"""
雪花算法 (Snowflake) 全局唯一 ID 生成器。

**Why**: 替代 UUID v4 作为所有持久化实体的主键生成策略。
- UUID 无序性导致 PostgreSQL B-Tree 索引页分裂、写入性能下降
- 雪花算法生成趋势递增的 64 位整数 ID，天然适合 B-Tree 索引
- ID 本身携带时间戳语义，可从中直接反解出生成时间

**位分配** (63 位有效位):
    41 位时间戳 (毫秒，自定义纪元) | 5 位数据中心 ID | 5 位工作节点 ID | 12 位序列号
    自定义纪元: 2024-01-01 00:00:00 UTC (可用约 69 年)

**时钟回拨防护**:
    - 回拨 ≤ 5ms: 自旋等待直到追上上一次时间戳
    - 回拨 > 5ms: 抛出 ClockBackwardsException，拒绝生成
    - 提供 `_clock_backwards` 状态标记供外部监控告警

**线程安全**: 使用 `threading.Lock` 保护 `_sequence` 和 `_last_timestamp` 的读写。

使用示例::

    from ai_werewolf_core.utils.snowflake import get_snowflake

    snowflake = get_snowflake()
    new_id: str = snowflake.next_id()       # "1478420056789123072"
    new_id_int: int = snowflake.next_id_int()  # 1478420056789123072
"""

import threading
import time
from typing import Optional

from ai_werewolf_core.config import settings
from ai_werewolf_core.utils.logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# 常量定义
# ============================================================================

# 自定义纪元: 2024-01-01 00:00:00 UTC (毫秒)
DEFAULT_EPOCH: int = 1704067200000

# 各段位宽
TIMESTAMP_BITS: int = 41
DATACENTER_BITS: int = 5
WORKER_BITS: int = 5
SEQUENCE_BITS: int = 12

# 最大值掩码
MAX_DATACENTER_ID: int = (1 << DATACENTER_BITS) - 1   # 31
MAX_WORKER_ID: int = (1 << WORKER_BITS) - 1           # 31
MAX_SEQUENCE: int = (1 << SEQUENCE_BITS) - 1           # 4095

# 左移偏移量
WORKER_SHIFT: int = SEQUENCE_BITS                      # 12
DATACENTER_SHIFT: int = SEQUENCE_BITS + WORKER_BITS    # 17
TIMESTAMP_SHIFT: int = SEQUENCE_BITS + WORKER_BITS + DATACENTER_BITS  # 22

# 时钟回拨容忍阈值 (毫秒)
CLOCK_BACKWARDS_TOLERANCE_MS: int = 5


# ============================================================================
# 异常定义
# ============================================================================

class ClockBackwardsException(Exception):
    """时钟回拨异常 —— 当系统时钟回拨超过容忍阈值时抛出。

    调用方应捕获此异常并采取降级措施，如：
    - 等待后重试
    - 切换备用 ID 生成策略
    - 触发告警通知运维
    """

    def __init__(self, last_timestamp: int, current_timestamp: int, drift_ms: int):
        self.last_timestamp = last_timestamp
        self.current_timestamp = current_timestamp
        self.drift_ms = drift_ms
        super().__init__(
            f"时钟回拨 {drift_ms}ms (上次={last_timestamp}, 当前={current_timestamp})，"
            f"超过容忍阈值 {CLOCK_BACKWARDS_TOLERANCE_MS}ms，拒绝生成 ID"
        )


# ============================================================================
# 雪花算法生成器
# ============================================================================

class SnowflakeGenerator:
    """雪花算法 ID 生成器。

    线程安全，支持时钟回拨检测与防护。
    使用模块级单例模式，推荐通过 :func:`get_snowflake` 获取实例。

    Args:
        datacenter_id: 数据中心 ID (0-31)
        worker_id: 工作节点 ID (0-31)
        epoch: 自定义起始纪元 (毫秒时间戳)
    """

    def __init__(
        self,
        datacenter_id: int,
        worker_id: int,
        epoch: int = DEFAULT_EPOCH,
    ):
        if not (0 <= datacenter_id <= MAX_DATACENTER_ID):
            raise ValueError(
                f"datacenter_id 必须在 0-{MAX_DATACENTER_ID} 之间，实际: {datacenter_id}"
            )
        if not (0 <= worker_id <= MAX_WORKER_ID):
            raise ValueError(
                f"worker_id 必须在 0-{MAX_WORKER_ID} 之间，实际: {worker_id}"
            )

        self._datacenter_id: int = datacenter_id
        self._worker_id: int = worker_id
        self._epoch: int = epoch

        # 状态字段 (受锁保护)
        self._sequence: int = 0
        self._last_timestamp: int = -1

        # 线程安全锁
        self._lock: threading.Lock = threading.Lock()

        # 监控指标
        self._clock_backwards: bool = False
        self._generated_count: int = 0

        logger.info(
            "雪花算法生成器已初始化",
            datacenter_id=datacenter_id,
            worker_id=worker_id,
            epoch=epoch,
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def next_id(self) -> str:
        """生成下一个全局唯一 ID，返回十进制字符串。

        Returns:
            19 位十进制数字字符串，如 ``"1478420056789123072"``
        """
        return str(self.next_id_int())

    def next_id_int(self) -> int:
        """生成下一个全局唯一 ID，返回原始整数。

        Returns:
            雪花算法生成的 64 位整数 ID

        Raises:
            ClockBackwardsException: 时钟回拨超过容忍阈值
        """
        with self._lock:
            timestamp = self._current_millis()

            # ---- 时钟回拨检测 ----
            if timestamp < self._last_timestamp:
                drift_ms = self._last_timestamp - timestamp
                if drift_ms <= CLOCK_BACKWARDS_TOLERANCE_MS:
                    # 轻微回拨: 自旋等待直到追上
                    self._clock_backwards = True
                    logger.warning(
                        "检测到轻微时钟回拨，自旋等待中",
                        drift_ms=drift_ms,
                        last_timestamp=self._last_timestamp,
                        current_timestamp=timestamp,
                    )
                    timestamp = self._wait_until_next_millis(self._last_timestamp)
                    self._clock_backwards = False
                else:
                    # 严重回拨: 拒绝生成
                    self._clock_backwards = True
                    logger.error(
                        "严重时钟回拨，拒绝生成 ID",
                        drift_ms=drift_ms,
                        last_timestamp=self._last_timestamp,
                        current_timestamp=timestamp,
                    )
                    raise ClockBackwardsException(
                        self._last_timestamp, timestamp, drift_ms
                    )

            # ---- 正常生成逻辑 ----
            if timestamp == self._last_timestamp:
                # 同一毫秒内，序列号递增
                self._sequence = (self._sequence + 1) & MAX_SEQUENCE
                if self._sequence == 0:
                    # 序列号用尽，等待下一毫秒
                    timestamp = self._wait_until_next_millis(self._last_timestamp)
            else:
                # 新的一毫秒，序列号重置
                self._sequence = 0

            self._last_timestamp = timestamp
            self._generated_count += 1

            # 位运算组装 ID
            snowflake_id = (
                ((timestamp - self._epoch) << TIMESTAMP_SHIFT)
                | (self._datacenter_id << DATACENTER_SHIFT)
                | (self._worker_id << WORKER_SHIFT)
                | self._sequence
            )

            return snowflake_id

    @property
    def clock_backwards(self) -> bool:
        """时钟回拨状态标记 (供外部监控使用)。"""
        return self._clock_backwards

    @property
    def generated_count(self) -> int:
        """已生成的 ID 总数 (监控指标)。"""
        return self._generated_count

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _current_millis(self) -> int:
        """获取当前 Unix 毫秒时间戳。"""
        return int(time.time() * 1000)

    def _wait_until_next_millis(self, last_timestamp: int) -> int:
        """自旋等待直到系统时间超过给定时间戳。"""
        timestamp = self._current_millis()
        while timestamp <= last_timestamp:
            timestamp = self._current_millis()
        return timestamp


# ============================================================================
# 单例工厂
# ============================================================================

_snowflake_instance: Optional[SnowflakeGenerator] = None
_instance_lock: threading.Lock = threading.Lock()


def get_snowflake() -> SnowflakeGenerator:
    """获取雪花算法生成器的全局单例。

    首次调用时使用 :mod:`ai_werewolf_core.config` 中的
    ``snowflake_datacenter_id`` 和 ``snowflake_worker_id`` 配置进行初始化。
    后续调用返回同一实例。

    Returns:
        全局唯一的 SnowflakeGenerator 实例
    """
    global _snowflake_instance

    if _snowflake_instance is None:
        with _instance_lock:
            # 双重检查锁定
            if _snowflake_instance is None:
                _snowflake_instance = SnowflakeGenerator(
                    datacenter_id=settings.snowflake_datacenter_id,
                    worker_id=settings.snowflake_worker_id,
                )

    return _snowflake_instance


def reset_snowflake() -> None:
    """重置雪花算法生成器单例 (仅用于测试)。"""
    global _snowflake_instance
    with _instance_lock:
        _snowflake_instance = None
        logger.debug("雪花算法生成器单例已重置")
