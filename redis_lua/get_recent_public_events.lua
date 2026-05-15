-- 获取指定流中的公共事件（按轮次过滤）
-- 参数:
--   KEYS[1]: 流键名
--   ARGV[1]: 最大轮次数，只返回轮次大于此值的事件
--   ARGV[2]: 查询的消息数量上限，默认为1000

local stream_key = KEYS[1]                           -- Redis流的键名
local max_round = tonumber(ARGV[1])                  -- 最大轮次数，只获取轮次大于此值的事件
local count = tonumber(ARGV[2]) or 1000              -- 查询的消息数量上限

-- 获取流中最新的消息
local messages = redis.call('XREVRANGE', stream_key, '+', '-', 'COUNT', count)
local result = {}                                    -- 存储符合条件的结果

-- 遍历每条消息
for i = 1, #messages do
    local msg_id = messages[i][1]                    -- 消息ID
    local fields = messages[i][2]                    -- 消息字段列表
    
    local visibility = ""                            -- 可见性（PUBLIC或PRIVATE）
    local event_type = ""                            -- 事件类型
    local payload_str = "{}"                         -- 有效载荷JSON字符串
    
    -- 解析消息字段
    for j = 1, #fields, 2 do
        if fields[j] == "visibility" then
            visibility = fields[j+1]                 -- 获取可见性
        elseif fields[j] == "event_type" then
            event_type = fields[j+1]                 -- 获取事件类型
        elseif fields[j] == "payload" then
            payload_str = fields[j+1]                -- 获取载荷
        end
    end
    
    local round = 9999                               -- 默认轮次设为9999
    local status, payload = pcall(cjson.decode, payload_str)  -- 尝试解析JSON载荷
    if status and type(payload) == "table" and payload["round"] then
        round = tonumber(payload["round"])           -- 如果解析成功且有round字段，则更新轮次
    end
    
    -- 如果当前消息的轮次小于等于最大轮次，则停止处理更早的消息
    if round <= max_round then
        break
    end
    
    -- 只保留公共可见性的特定类型事件
    if visibility == "PUBLIC" and (event_type == "SPEECH_EVENT" or event_type == "VOTE_EVENT" or event_type == "PLAYER_DEATH") then
        table.insert(result, messages[i])            -- 添加到结果列表
    end
end

-- 将结果反转以按时间顺序排列（从最旧到最新）
local reversed_result = {}
for i = #result, 1, -1 do
    table.insert(reversed_result, result[i])         -- 反转数组使时间顺序正确
end

return reversed_result                               -- 返回过滤后的公共事件列表