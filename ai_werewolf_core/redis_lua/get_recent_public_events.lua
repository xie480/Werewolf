local stream_key = KEYS[1]
local max_round = tonumber(ARGV[1])
local count = tonumber(ARGV[2]) or 1000

local messages = redis.call('XREVRANGE', stream_key, '+', '-', 'COUNT', count)
local result = {}

for i = 1, #messages do
    local msg_id = messages[i][1]
    local fields = messages[i][2]
    
    local visibility = ""
    local event_type = ""
    local payload_str = "{}"
    
    for j = 1, #fields, 2 do
        if fields[j] == "visibility" then
            visibility = fields[j+1]
        elseif fields[j] == "event_type" then
            event_type = fields[j+1]
        elseif fields[j] == "payload" then
            payload_str = fields[j+1]
        end
    end
    
    local round = 9999
    local status, payload = pcall(cjson.decode, payload_str)
    if status and type(payload) == "table" and payload["round"] then
        round = tonumber(payload["round"])
    end
    
    if round <= max_round then
        break
    end
    
    if visibility == "PUBLIC" and (event_type == "SPEECH_EVENT" or event_type == "VOTE_EVENT" or event_type == "PLAYER_DEATH") then
        table.insert(result, messages[i])
    end
end

-- Reverse the result to make it chronological (oldest to newest)
local reversed_result = {}
for i = #result, 1, -1 do
    table.insert(reversed_result, result[i])
end

return reversed_result
