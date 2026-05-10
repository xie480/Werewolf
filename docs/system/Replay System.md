**核心结论先行：**
回放系统绝对**不是**录制视频，也**不需要**后端重新跑一遍大模型。
由于我们在前面设计了基于 `Event Sourcing（事件溯源）` 的 Event System 和 Persistence System，回放系统本质上就是一个 **“事件播放器（Event Player）”**。前端拿到按时间序列排好的 JSON 数组，通过一个轻量级的“状态归约器（Reducer）”逐帧渲染即可。
以下是专为 AI 狼人杀设计的高扩展性 Replay System 落地方案：
---
### 一、 回放系统的核心架构
回放系统分为后端数据提供层和前端渲染控制层：
1. **Backend (Data Provider)**：负责根据请求的视角（上帝视角/特定玩家视角），从 `game_events` 表中拉取事件，按 `seq_id` 严格排序，并按“天数/阶段”进行打包（Chunking）后下发。
2. **Frontend (Event Player)**：维护一个轻量级的本地状态机（初始状态为全员存活），通过定时器`setInterval`）按顺序消费 Event 数组，驱动 UI 变化（如头像变灰、弹出聊天气泡）。
---
### 二、 关键特性：多视角隔离 (Perspective Control)
回放系统最大的亮点是支持“切视角”。由于狼人杀是信息不对称游戏，观战和复盘必须支持以下两种模式：
#### 1. 上帝视角模式 (God Mode)
* **适用场景**：全局复盘、向评委/观众展示 AI 的高智商博弈。
* **数据拉取规则**：拉取该 `game_id` 下的所有事件，无视 `visibility` 限制。
* **UI 表现**：全场玩家底牌翻开，夜晚能看到狼人频道的聊天和预言家的查验结果。
#### 2. 第一人称视角模式 (POV Mode / First-Person)
* **适用场景**：用于 Debug 某个 Agent 为什么“犯蠢”（比如排查它是不是没收到某个关键信息），或者让人类玩家代入 AI 的视角体验局势。
* **数据拉取规则**：
  ```sql
  -- 伪SQL逻辑：只拉取公开事件，以及专属该 Agent 的私密/阵营事件
  SELECT * FROM game_events
  WHERE game_id = 'g_123'
  AND (visibility = 'PUBLIC'
       OR (visibility = 'PRIVATE' AND target_agent = 'player_5')
       OR (visibility = 'FACTION' AND faction = 'WEREWOLF'))
  ORDER BY seq_id ASC;
  ```
---
### 三、 核心 API 协议设计 (RESTful Schema)
由于一局完整的对局可能有上百个 Event，建议后端接口按“天 (Day)”进行数据分组，方便前端做进度条（Timeline）的章节划分。
**接口`GET /api/v1/replay/{game_id}?perspective=GOD`**
**返回 JSON 结构：**
```json
{
  "game_id": "game_1001",
  "perspective": "GOD",
  "initial_state": {
    "players": [
      {"agent_id": "player_1", "role": "WEREWOLF", "avatar": "url..."},
      {"agent_id": "player_2", "role": "SEER", "avatar": "url..."}
    ]
  },
  "timeline": [
    {
      "day_num": 1,
      "phases": [
        {
          "phase_name": "NIGHT_ACTION",
          "events": [
             // 继承自 Event System 的标准化结构
             {"seq_id": 1, "type": "WOLF_CHAT", "actor": "player_1", "content": "今晚刀2号吧？"},
             {"seq_id": 2, "type": "WOLF_KILL", "target": "player_2"}
          ]
        },
        {
          "phase_name": "DAY_DISCUSSION",
          "events": [
             {"seq_id": 15, "type": "SYSTEM_ANNO", "content": "昨夜，2号玩家死亡。"},
             {"seq_id": 16, "type": "SPEECH", "actor": "player_3", "content": "2号死了，我怀疑1号是狼..."}
          ]
        }
      ]
    }
  ]
}
```
---
### 四、 前端状态归约器设计 (Frontend Reducer)
这是前端回放的核心难点：**如何实现“快进、快退、跳过本轮”？**
绝对不能把前端动画和游戏逻辑强绑定。前端必须实现一个 `Event Reducer`（类似于 Redux 的理念）。
**逻辑管线：**
1. **播放指针 (Cursor)**：前端记录当前播放到了哪个 `seq_id`。
2. **计算当前帧状态**：如果用户突然把进度条拖到 `seq_id = 50`，前端必须拿 `initial_state`（全员存活），然后在内存中瞬间执行一遍从 `seq_id = 1` 到 `50` 的所有事件，计算出 `seq_id = 50` 时的最新状态（比如：2号已死，5号已发言），然后根据这个最新状态去渲染 UI。
3. **播放动画**：当进度条正常步进`seq_id = 50 -> 51`）时，读取 51 号事件。如果是 `SPEECH`，则在 UI 上给该角色弹出一个打字机效果的对话气泡；如果是 `DEATH`，则播放头像碎裂动画。
```javascript
// 前端 Reducer 伪代码
function calculateGameState(initialState, events, currentSeqId) {
    let state = clone(initialState);
    for (let event of events) {
        if (event.seq_id > currentSeqId) break;

        switch (event.type) {
            case 'DEATH':
                state.players[[event.target](http://event.target)].isAlive = false;
                break;
            case 'VOTED_OUT':
                state.players[[event.target](http://event.target)].isAlive = false;
                break;
            // SPEECH 等事件不改变存活状态，只需用来在 UI 上展示历史气泡
        }
    }
    return state;
}
```
---
### 五、 高级功能：内部OS（内心戏）透视
普通的狼人杀只能看到别人说了什么，但我们做的是 AI 系统，最牛的展示点是**展示 AI 的思考过程**。
*   **数据结合**：在生成回放数据时，后端需要把 Evaluator / Memory 里的 `Belief State` (内部推理想法) 挂载到每一次 `SPEECH` 或 `VOTE` 事件旁边。
*   **UI 展示**：在前端播放回放时，角色头顶弹出对话气泡说：“我觉得 5 号是好人”，但在气泡旁边可以放一个半透明的“内心 OS 侧边栏”，显示该 Agent 的真实推理日志：“内部思考：5号其实是狼队友，我现在假装保他做倒钩”。
*   **效果**：这种“表里不一”的反差感，是展示大模型推理能力（Chain of Thought）和伪装能力最直观的利器！
---
### 六、 风险点与防坑指南
1. **时序错乱导致前端崩溃**
   * **坑点**：如果数据库里事件的 `seq_id` 乱了，导致 `DEATH` 事件排在 `SPEECH` 前面，前端 Reducer 就会发现“一个死人发了言”，可能引发 UI 逻辑崩溃。
   * **解决方案**：强依赖我们在 `Event System` 中设计的“数据库自增 ID / 全局唯一时钟”，在提供 API 时严格执行 `ORDER BY seq_id ASC`。
2. **前端长文本渲染卡顿**
   * **坑点**：AI 白天的发言往往很长（几百字），如果使用打字机效果，播放时间太长会导致回放极其拖沓。
   * **解决方案**：前端提供“倍速播放（1x, 2x, 4x）”功能；同时允许用户点击气泡直接“Skip（跳过打字动画直接显示全段）”。