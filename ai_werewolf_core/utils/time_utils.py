from datetime import datetime, timezone, timedelta

# 东八区时区 (UTC+8)
TZ_UTC_8 = timezone(timedelta(hours=8), name="Asia/Shanghai")

def now_tz() -> datetime:
    """获取当前东八区时间"""
    return datetime.now(TZ_UTC_8)
