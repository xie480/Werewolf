-- status_transition.lua
-- 原子全局游戏状态迁移，带 CAS（Compare-And-Swap）语义。
-- 读取当前全局状态，根据合法跳转表校验迁移路径，
-- 并在单次原子操作中更新状态。
-- 消除 LifecycleManager 中 HGET 检查与 HSET 更新之间的竞态条件。
--
-- KEYS[1]: 对局上下文 Hash Key
-- ARGV[1]: 期望的当前状态（字符串）
-- ARGV[2]: 目标新状态（字符串）
-- ARGV[3]: 合法跳转表 JSON:
--           {"old_status_value": ["new_status1", "new_status2", ...], ...}
--
-- 返回值: {status, old_status, new_status}
--   status: "OK"（成功）| "INVALID_TRANSITION"（非法跳转）| "STATUS_MISMATCH"（状态不匹配）

local key = KEYS[1]
local expected_status = ARGV[1]
local target_status = ARGV[2]

local cjson = require "cjson"
local allowed = cjson.decode(ARGV[3])

local current = redis.call('HGET', key, 'status')
if not current then
    current = 'INIT'
end

-- 检查当前状态是否与调用方期望的一致
if current ~= expected_status then
    return {'STATUS_MISMATCH', current, target_status}
end

-- 检查跳转路径是否合法
local allowed_list = allowed[current]
if not allowed_list then
    return {'INVALID_TRANSITION', current, target_status}
end

local valid = false
for _, s in ipairs(allowed_list) do
    if s == target_status then
        valid = true
        break
    end
end

if not valid then
    return {'INVALID_TRANSITION', current, target_status}
end

-- 原子执行迁移
redis.call('HSET', key, 'status', target_status)
return {'OK', current, target_status}
