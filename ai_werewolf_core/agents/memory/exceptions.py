"""Agent Memory System 异常类定义。"""

class MemorySystemError(Exception):
    """记忆系统基础异常类"""
    pass

class MemoryNotFoundError(MemorySystemError):
    """未找到记忆数据异常"""
    pass

class SecurityViolationException(MemorySystemError):
    """越权访问记忆异常
    
    当 Agent 试图读取或修改不属于自己的私有记忆时抛出。
    """
    pass
