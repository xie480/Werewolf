-- vote_submit.lua
-- 原子投票提交，带重复投票检测。
-- 在一次原子操作中完成：检查投票人是否已投票、记录选票、设置 TTL。
-- 消除多 Worker 部署下 HEXISTS 检查与 HSET 写入之间的 TOCTOU 竞态条件。
--
-- KEYS[1]: 投票 Hash Key
-- ARGV[1]: 投票人 ID
-- ARGV[2]: 被投目标 ID（空字符串表示弃权）
-- ARGV[3]: TTL（秒）
--
-- 返回值: {had_previous, target_value}
--   had_previous: 1 表示该投票人之前已有投票记录，0 表示首次投票
--   target_value: 记录的投票目标（字符串）

local key = KEYS[1]
local voter = ARGV[1]
local target = ARGV[2]
local ttl = tonumber(ARGV[3])

local had_previous = redis.call('HEXISTS', key, voter)
redis.call('HSET', key, voter, target)
redis.call('EXPIRE', key, ttl)

return {had_previous, target}
