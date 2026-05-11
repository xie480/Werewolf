-- hset_with_ttl.lua
-- 原子 HSET + EXPIRE 操作。
-- 将 Hash 字段设置与 Key 过期时间设置合并为单次原子操作，
-- 防止进程在两个命令之间崩溃导致 Key 永不过期。
--
-- KEYS[1]: Hash Key
-- ARGV[1]: 字段名
-- ARGV[2]: 字段值
-- ARGV[3]: TTL（秒）
--
-- 返回值: 1（成功）

redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
