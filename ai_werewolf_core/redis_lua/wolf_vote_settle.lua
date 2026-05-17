-- wolf_vote_settle.lua
-- 原子结算狼人投票，带 CAS（Compare-And-Swap）语义。
-- 在单次原子操作中完成：检查状态 → 关闭投票 → 收集选票 → 统计结果。
-- 防止多 Worker 并发结算导致重复处理。
--
-- KEYS[1]: 狼人投票 Hash Key
-- ARGV[1]: TTL（秒）
-- ARGV[2]: 当前时间戳（ISO 格式，用于审计时间线）
--
-- 返回值: {status, vote_count_json, vote_details_json}
--   status: "OK"（结算成功）| "ALREADY_SETTLED"（已结算，跳过）
--   vote_count_json: JSON 对象 {target_id: 得票数, ...}
--   vote_details_json: JSON 对象 {voter_id: target_id, ...}

local key = KEYS[1]
local ttl = tonumber(ARGV[1])
local timestamp = ARGV[2]

-- 检查是否已结算（防重入）
local current_status = redis.call('HGET', key, 'meta:status')
if current_status == 'SETTLED' then
    return {'ALREADY_SETTLED', nil, nil}
end

-- 关闭投票回合（阻止新投票提交）
redis.call('HSET', key, 'meta:status', 'CLOSED')
redis.call('HSET', key, 'meta:vote_end_at', timestamp)
redis.call('EXPIRE', key, ttl)

-- 收集所有非 meta 字段的选票
local all_fields = redis.call('HGETALL', key)
local votes = {}
local vote_details = {}

for i = 1, #all_fields, 2 do
    local field = all_fields[i]
    local value = all_fields[i + 1]
    -- 跳过 meta:* 字段
    if not string.match(field, '^meta:') then
        local count = votes[value] or 0
        votes[value] = count + 1
        vote_details[field] = value
    end
end

-- 打平到 JSON 字符串返回
local cjson = require 'cjson'
return {'OK', cjson.encode(votes), cjson.encode(vote_details)}
