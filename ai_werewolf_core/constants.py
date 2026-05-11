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

    ALIVE_BITMAP_PREFIX: str = "werewolf:alive"
    """玩家存活状态 BitMap Key 前缀。完整格式: werewolf:alive:{game_id}"""

    # ------------------------------------------------------------------
    # Key 构建静态方法
    # ------------------------------------------------------------------

    @staticmethod
    def seq(game_id: str) -> str:
        """构建时序发号器 Key: werewolf:seq:{game_id}"""
        return f"{RedisKeys.SEQ_PREFIX}:{game_id}"

    @staticmethod
    def event_stream(game_id: str) -> str:
        """构建事件 Stream Key: werewolf:events:{game_id}"""
        return f"{RedisKeys.EVENT_STREAM_PREFIX}:{game_id}"

    @staticmethod
    def vote_hash(game_id: str, round_num: int) -> str:
        """构建投票 Hash Key: werewolf:vote:{game_id}:{round}"""
        return f"{RedisKeys.VOTE_HASH_PREFIX}:{game_id}:{round_num}"

    @staticmethod
    def game_context(game_id: str) -> str:
        """构建对局上下文 Hash Key: werewolf:game:{game_id}:context"""
        return f"{RedisKeys.GAME_CONTEXT_PREFIX}:{game_id}:context"

    @staticmethod
    def player_info(game_id: str) -> str:
        """构建玩家身份 Hash Key: werewolf:players:{game_id}"""
        return f"{RedisKeys.PLAYER_INFO_PREFIX}:{game_id}"

    @staticmethod
    def alive_bitmap(game_id: str) -> str:
        """构建存活状态 BitMap Key: werewolf:alive:{game_id}"""
        return f"{RedisKeys.ALIVE_BITMAP_PREFIX}:{game_id}"
