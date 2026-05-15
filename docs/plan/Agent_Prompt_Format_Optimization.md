# Agent Prompt Format 优化方案

## 背景与问题
当前的 `format.j2` 模板过于通用，导致在不同游戏阶段和不同阵营下，Agent 可能会产生幻觉或输出不符合当前上下文的内容。主要问题包括：
1. **动作类型不明确**：在只能投票的环节，Agent 可能会输出 `SPEECH` 或其他非法动作。
2. **发言字段冗余**：在不允许发言的环节（如投票、夜晚），保留 `speech_content` 字段会导致 Agent 强行输出废话，浪费 Token 并增加延迟。
3. **嫌疑度语义混淆**：好人阵营的 `suspect_list` 是“狼人嫌疑度”，而狼人阵营的应该是“神职嫌疑度”。统一的描述会导致狼人 Agent 逻辑混乱。

## 优化方案
采用 Jinja2 条件渲染，根据当前游戏阶段（Phase）、阵营（Faction）和允许的动作（Allowed Actions）动态生成 JSON Schema。

### 1. 动态渲染 `format.j2`
修改 `ai_werewolf_core/agents/prompts/templates/format.j2`，引入以下变量：
- `faction`: 当前 Agent 的阵营（`VILLAGER` 或 `WEREWOLF`）。
- `allowed_actions`: 当前阶段允许的动作列表。
- `can_speak`: 当前阶段是否允许发言。

模板逻辑：
- 根据 `faction` 区分 `suspect_list` 的描述。
- 根据 `allowed_actions` 动态限制 `action_type` 的可选值。如果只有一个可选值，则直接硬编码。
- 根据 `can_speak` 决定是否输出 `speech_content` 字段。

### 2. 修改 `PromptBuilder`
在 `ai_werewolf_core/agents/prompts/builder.py` 中，修改 `_render_format` 方法，使其接收并传递上述变量。
需要根据 `current_phase` 和 `snapshot.private_state.role` 计算 `allowed_actions` 和 `can_speak`。

#### 动作与发言权限映射规则
- **发言阶段** (`DAY_DISCUSSION`, `DAY_PK_DISCUSSION`, `LAST_WORDS`):
  - `can_speak`: True
  - `allowed_actions`: `['SPEAK']`
- **投票阶段** (`DAY_VOTE`, `DAY_PK_VOTE`):
  - `can_speak`: False
  - `allowed_actions`: `['VOTE']`
- **夜晚行动阶段**:
  - `can_speak`: False
  - `allowed_actions`: 根据角色和阶段决定（如狼人在 `NIGHT_WOLF_ACT` 可以 `WOLF_KILL` 或 `PASS`，女巫在 `NIGHT_WITCH_ACT` 可以 `WITCH_SAVE`, `WITCH_POISON`, `PASS` 等）。如果不是当前角色的行动阶段，则为 `['PASS']`。

### 3. 兼容性检查
- `reasoning_node.py` 中的 `AgentResponseSchema` 已经将 `speech_content` 和 `action_target` 设为 `Optional`，因此动态移除这些字段不会导致 Pydantic 解析失败。

## 实施步骤
1. 编写并提交本规划文档。
2. 切换到 Code 模式。
3. 修改 `PromptBuilder` (`ai_werewolf_core/agents/prompts/builder.py`)。
4. 修改 `format.j2` (`ai_werewolf_core/agents/prompts/templates/format.j2`)。
5. 运行相关测试验证 Prompt 生成逻辑。
