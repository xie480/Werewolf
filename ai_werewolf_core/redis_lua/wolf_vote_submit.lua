-- wolf_vote_submit.lua
-- 狼人原子投票提交，带状态检测、重复投票检测和时间戳记录。
-- 在单次原子操作中完成：检查投票回合状态 → 记录选票 → 设置 TTL。
-- 消除多 Worker 部署下并发投票的竞态条件。
--
-- KEYS[1]: 狼人投票 Hash Key
-- ARGV[1]: 投票人 ID
-- ARGV[2]: 被投目标 ID（空字符串表示弃权）
-- ARGV[3]: TTL（秒）
-- ARGV[4]: 当前时间戳（ISO 格式，用于审计）
--
-- 返回值: {status, voter, previous_target, vote_count}
--   status: "OK"（成功）| "CLOSED"（投票回合已关闭或已结算）
--   voter: 投票人 ID
--   previous_target: 该投票人之前的投票目标（首次投票为空字符串）
--   vote_count: 当前非 meta 字段的选票数量

local key = KEYS[1]
local voter = ARGV[1]
local target = ARGV[2]
local ttl = tonumber(ARGV[3])
local timestamp = ARGV[4]

-- 检查投票回合是否已关闭或已结算
local vote_status = redis.call('HGET', key, 'meta:status')
if vote_status == 'CLOSED' or vote_status == 'SETTLED' then
    return {'CLOSED', voter, '', '0'}
end

-- 记录选票
local previous = redis.call('HGET', key, voter)
redis.call('HSET', key, voter, target)
redis.call('EXPIRE', key, ttl)

-- 如果是首次投票，记录投票开始时间戳
if not previous then
    local existing_start_at = redis.call('HGET', key, 'meta:vote_start_at')
    if not existing_start_at then
        redis.call('HSET', key, 'meta:vote_start_at', timestamp)
    end
end

-- 计算当前非 meta 字段的选票数量
local all_fields = redis.call('HGETALL', key)
local actual_vote_count = 0
for i = 1, #all_fields, 2 do
    local field_name = all_fields[i]
    if not string.match(field_name, '^meta:') then
        actual_vote_count = actual_vote_count + 1
    end
end

return {'OK', voter, previous or '', tostring(actual_vote_count)}
