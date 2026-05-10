**核心结论先行：**
在纯 AI 对局中，前端只是“观战屏幕”；但如果我们要支持**“人机混战 (Human-in-the-loop)”**或**“管理员干预”**，前端就必须是一套高度动态的**上下文感知控制台（Context-Aware UI）**。
前端交互系统不能把规则写死在界面里，它的渲染逻辑必须严格依赖后端推送的 `GamePhase` 和玩家自身的 `Role/Permission`。
以下是专为 LangGraph 狼人杀设计的前端交互系统落地方案：
---
### 一、 人机协作与混合对局 (Human-in-the-loop)
LangGraph 的一大优势是支持 `interrupt_before`（打断与人类干预）。当真人类玩家加入游戏时，前端需要提供一套与 AI Agent 格式完全对齐的交互组件。
#### 1. 动态行动面板 (Dynamic Action Panel)
在页面的底部或目标玩家的头像上，必须根据**当前阶段**和**人类玩家的底牌**动态渲染操作按钮：
*   **白天发言 (DAY_DISCUSSION)**：
    *   渲染一个带字数限制的输入框（Textarea）和录音按钮（可通过 Web Speech API 转文本）。
    *   提交按钮点击后，前端将内容打包成 `{"action_type": "SPEAK", "speech_content": "..."}` 发给后端。
*   **白天投票 (DAY_VOTE)**：
    *   全场存活玩家头像上出现“投票 (Vote)”按钮。人类自身头像旁边出现“弃权 (Pass)”按钮。
*   **夜晚技能 (NIGHT_ACTION - 假设人类是女巫)**：
    *   根据后端传来的状态判断是否有药。如果此时有解药且检测到有人被刀，弹出弹窗：“昨夜X号被刀，是否使用解药？[是/否]”。
#### 2. 时间槽与进度条 (Time Window)
在人机混战中，AI 响应只需 2 秒，但人类需要时间思考。
前端必须在屏幕中央渲染一个**倒计时全局进度条**（如 60 秒）。当时间耗尽时，前端必须强制禁用交互面板，并向后端发送一个默认兜底动作（如 `PASS`）。
---
### 二、 导演/管理员控制台 (Director Desk)
为了方便测试、演示以及掌控全局，前端需要为管理员设计一个悬浮的“上帝控制台”：
*   **对局生命周期控制**：提供 `[Start (开始)][Pause (暂停)][Resume (恢复)][Abort (强行终止)]` 四个硬核控制按钮。
    *   _注：点击 Pause 时，前端通过 API 通知后端，后端引擎会在当前 Phase 结束后挂起，不再流转进入下一个 Phase，直到收到 Resume。_
*   **AI 降智/加速滑块 (Tempo Control)**：
    *   如果为了演示看清过程，可以设置 `delay_per_action = 5s`；如果为了快速刷几百局看胜率，可以设置 `delay = 0` 并关闭前端渲染。
---
### 三、 WebSocket 双向通信协议设计
前端交互极度依赖 WebSocket（WS）。必须区分**上行指令（Upstream）**和**下行事件（Downstream）**。
#### 1. 下行事件 (Backend -> Frontend)
复用我们之前设计的 Event System 格式，前端主要监听状态刷新：
```json
{
  "type": "STATE_SYNC", // 或 EVENT_BROADCAST
  "seq_id": 42,
  "payload": {
    "current_phase": "DAY_VOTE",
    "required_action_from": ["player_human_1"] // 关键！告诉前端需要唤醒人类玩家的输入面板
  }
}
```
#### 2. 上行指令 (Frontend -> Backend)
前端收集用户的点击/输入，封装成标准的 `AgentAction` 交给后端。
```json
{
  "type": "SUBMIT_ACTION",
  "game_id": "game_1001",
  "client_id": "player_human_1",
  "payload": {
    "action_type": "WOLF_KILL",
    "target_id": "player_3"
  }
}
```
---
### 四、 乐观更新与交互容错 (Optimistic UI & Resilience)
交互系统的最大痛点在于网络延迟。如果人类玩家点击了“投票给 3 号”，在等待后端校验返回的 1 秒内，前端如果毫无反应，用户会狂点。
#### 1. 乐观 UI (Optimistic UI) 机制
当用户提交动作（如发言）时，前端**不要等待后端的成功回包**：
1. 立刻在聊天时间轴上渲染一条带“转圈/发送中”虚线状态的发言气泡。
2. 禁用发送按钮，防止重复提交。
3. 当收到后端 Event Bus 广播的正式 `SPEECH_EVENT` 且 `seq_id` 匹配时，将虚线状态转为正式状态。
4. 若后端校验拒绝（如包含敏感词或阶段不对），气泡变红，提示报错信息并允许修改。
#### 2. 断线重连 (Reconnection & State Recovery)
*   **现象**：用户切到微信再切回浏览器，WebSocket 断开。
*   **交互方案**：前端监听到 `onclose` 事件，屏幕蒙上一层半透明黑色遮罩，显示“与牌桌断开连接，正在重连...”。
*   **恢复逻辑**：WS 重新连接后，前端携带本地最后一次成功接收的 `last_seq_id` 向后端发起同步请求，后端下发丢失的 Event 列表，前端 Reducer 快速追帧，瞬间恢复到最新状态，撤掉遮罩。