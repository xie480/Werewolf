"""全局常量定义。

集中管理项目中使用的各类常量，如 Redis Key 前缀等。
"""

class RedisKeys:
    """Redis Key 前缀常量 —— 唯一数据来源。

    **Why**: 所有 Redis Key 的组装必须使用此类中定义的常量或静态方法，
    严禁在业务代码中硬编码 Key 字符串。这确保 Key 命名的一致性，
    并便于后续 Key 治理（如批量过期、迁移、监控）。
    """

    # ------------------------------------------------------------------
    # Key 前缀常量
    # ------------------------------------------------------------------

    SEQ_PREFIX: str = "werewolf:seq"
    """全局时序发号器 Key 前缀。完整格式: werewolf:seq:{game_id}"""

    EVENT_STREAM_PREFIX: str = "werewolf:events"
    """事件热数据 Stream Key 前缀。完整格式: werewolf:events:{game_id}"""

    VOTE_HASH_PREFIX: str = "werewolf:vote"
    """投票数据 Hash Key 前缀。完整格式: werewolf:vote:{game_id}:{round}"""

    GAME_CONTEXT_PREFIX: str = "werewolf:game"
    """对局上下文 Hash Key 前缀。完整格式: werewolf:game:{game_id}:context"""

    PLAYER_INFO_PREFIX: str = "werewolf:players"
    """玩家身份信息 Hash Key 前缀。完整格式: werewolf:players:{game_id}"""

    GAME_TASK_PREFIX: str = "werewolf:task"
    """Celery 任务 ID Hash Key 前缀。完整格式: werewolf:task:{game_id}:current_task"""

    ALIVE_BITMAP_PREFIX: str = "werewolf:alive"
    """玩家存活状态 BitMap Key 前缀。完整格式: werewolf:alive:{game_id}"""

    PRIVATE_MEMORY_PREFIX: str = "werewolf:memory:private"
    """Agent 私有记忆 Hash Key 前缀。完整格式: werewolf:memory:private:{game_id}:{player_id}"""

    # ------------------------------------------------------------------
    # Key 构建静态方法
    # ------------------------------------------------------------------

    @staticmethod
    def seq(game_id: str) -> str:
        """构建时序发号器 Key: werewolf:seq:{game_id}
        存储结构: String
        内容结构: Integer (全局递增序号，用于 INCR 原子递增)
        """
        return f"{RedisKeys.SEQ_PREFIX}:{game_id}"

    @staticmethod
    def event_stream(game_id: str) -> str:
        """构建事件 Stream Key: werewolf:events:{game_id}
        存储结构: Stream
        内容结构: Event 模型的字段字典 (event_id, game_id, seq_num, event_type, visibility, target_agents, timestamp, payload)
        """
        return f"{RedisKeys.EVENT_STREAM_PREFIX}:{game_id}"

    @staticmethod
    def vote_hash(game_id: str, round_num: int) -> str:
        """构建投票 Hash Key: werewolf:vote:{game_id}:{round}
        存储结构: Hash
        内容结构: Field 为 player_id, Value 为 VoteContent 模型的 JSON 字符串
        """
        return f"{RedisKeys.VOTE_HASH_PREFIX}:{game_id}:{round_num}"

    @staticmethod
    def game_context(game_id: str) -> str:
        """构建对局上下文 Hash Key: werewolf:game:{game_id}:context
        存储结构: Hash
        内容结构: Field 包含 phase (GamePhase), round (int) 等状态机上下文信息
        """
        return f"{RedisKeys.GAME_CONTEXT_PREFIX}:{game_id}:context"

    @staticmethod
    def player_info(game_id: str) -> str:
        """构建玩家身份 Hash Key: werewolf:players:{game_id}
        存储结构: Hash
        内容结构: Field 为 player_id, Value 为 Player 模型的 JSON 字符串
        """
        return f"{RedisKeys.PLAYER_INFO_PREFIX}:{game_id}"

    @staticmethod
    def alive_bitmap(game_id: str) -> str:
        """构建存活状态 BitMap Key: werewolf:alive:{game_id}
        存储结构: String (作为 BitMap 使用)
        内容结构: 二进制位 (0/1)，偏移量(offset)为玩家的 seat_number
        """
        return f"{RedisKeys.ALIVE_BITMAP_PREFIX}:{game_id}"

    @staticmethod
    def game_task_id(game_id: str) -> str:
        """构建 Celery 任务 ID 存储 Key: werewolf:task:{game_id}:current_task
        存储结构: String
        内容结构: Celery Task ID (UUID 字符串)
        """
        return f"{RedisKeys.GAME_TASK_PREFIX}:{game_id}:current_task"

    @staticmethod
    def private_memory(game_id: str, player_id: str) -> str:
        """构建 Agent 私有记忆 Hash Key: werewolf:memory:private:{game_id}:{player_id}
        存储结构: Hash
        内容结构: Field 为 state, Value 为 PrivateState 模型的 JSON 字符串 (不包含 system_feedbacks)
        """
        return f"{RedisKeys.PRIVATE_MEMORY_PREFIX}:{game_id}:{player_id}"

    @staticmethod
    def private_memory_feedbacks(game_id: str, player_id: str) -> str:
        """构建 Agent 私有记忆反馈 List Key: werewolf:memory:private:{game_id}:{player_id}:feedbacks
        存储结构: List
        内容结构: PrivateEventLog 模型的 JSON 字符串列表
        """
        return f"{RedisKeys.PRIVATE_MEMORY_PREFIX}:{game_id}:{player_id}:feedbacks"

    @staticmethod
    def private_memory_reasoning(game_id: str, player_id: str) -> str:
        """构建 Agent 私有记忆推理 List Key: werewolf:memory:private:{game_id}:{player_id}:reasoning
        存储结构: List
        内容结构: 字符串列表 (Agent 的内心 OS 文本)
        """
        return f"{RedisKeys.PRIVATE_MEMORY_PREFIX}:{game_id}:{player_id}:reasoning"

    @staticmethod
    def private_memory_suspect_list(game_id: str, player_id: str) -> str:
        """构建 Agent 私有记忆嫌疑人列表 Hash Key: werewolf:memory:private:{game_id}:{player_id}:suspect_list
        存储结构: Hash
        内容结构: Field 为 target_player_id, Value 为嫌疑度 (float 字符串)
        """
        return f"{RedisKeys.PRIVATE_MEMORY_PREFIX}:{game_id}:{player_id}:suspect_list"
