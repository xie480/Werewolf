"""全局常量定义。

集中管理项目中使用的各类常量，如 Redis Key 前缀等。
"""

class RedisKeys:
    """Redis Key 前缀常量。"""
    
    SEQ_PREFIX: str = "werewolf:seq"
    """全局时序发号器 Key 前缀。完整格式: werewolf:seq:{game_id}"""
