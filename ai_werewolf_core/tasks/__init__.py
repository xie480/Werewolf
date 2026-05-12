"""
Celery 异步任务模块。

按任务类型分模块组织：
- game: 对局生命周期任务（结算、胜负判定、阶段推进）
- agent: Agent 推理任务（Phase 4 完成后实现）
- eval: 评测统计任务（Phase 5 完成后实现）
"""
