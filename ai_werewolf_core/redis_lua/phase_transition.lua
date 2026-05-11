-- phase_transition.lua
-- 原子游戏阶段迁移，带 CAS（Compare-And-Swap）语义。
-- 从 Redis 加载当前阶段，根据 JSON 格式的合法跳转表校验迁移路径，
-- 并在单次原子操作中更新阶段和轮次。
-- 防止多 Worker 部署下并发阶段迁移导致的状态损坏。
--
-- KEYS[1]: 对局上下文 Hash Key
-- ARGV[1]: 期望的当前阶段（字符串，"None" 表示空）
-- ARGV[2]: 目标新阶段（字符串）
-- ARGV[3]: 新轮次号（字符串）
-- ARGV[4]: 合法跳转表 JSON:
--           {"old_phase_value": ["new_phase1", "new_phase2", ...], ...}
--
-- 返回值: {status, old_phase, new_phase}
--   status: "OK"（成功）| "INVALID_TRANSITION"（非法跳转）| "PHASE_MISMATCH"（阶段不匹配）

local key = KEYS[1]
local expected_phase = ARGV[1]
local target_phase = ARGV[2]
local new_round = ARGV[3]

local cjson = require "cjson"
local allowed = cjson.decode(ARGV[4])

local current = redis.call('HGET', key, 'phase')
if not current then
    current = 'None'
end

-- 检查当前阶段是否与调用方期望的一致
if current ~= expected_phase then
    return {'PHASE_MISMATCH', current, target_phase}
end

-- 检查跳转路径是否合法
local allowed_list = allowed[current]
if not allowed_list then
    allowed_list = allowed['None'] or {}
end

local valid = false
for _, p in ipairs(allowed_list) do
    if p == target_phase then
        valid = true
        break
    end
end

if not valid then
    return {'INVALID_TRANSITION', current, target_phase}
end

-- 原子执行迁移
redis.call('HSET', key, 'phase', target_phase, 'round', new_round)
return {'OK', current, target_phase}
