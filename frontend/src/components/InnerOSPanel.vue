<script setup lang="ts">
/**
 * 内心 OS 透视面板 —— 展示 AI 玩家真实推理过程的悬浮窗。
 *
 * **Why**: 纯人机对局中，观众以 GOD 视角观战时可以看到每个 AI 玩家
 * 公开表面发言背后的真实内心 OS（内部推理过程），体现"表里不一"的反差感。
 * 这是展示大模型 Chain of Thought (思维链) 能力的最直观方式。
 *
 * 使用场景:
 * - 在 ReplayBoard 回放中：从 ReplayStore 的 innerThoughts 获取数据
 * - 在 GameBoard 实时对局中：从 GameStore 的 events 中提取 inner_thought
 *
 * 参考:
 * - [`docs/plan/前端界面设计方案.md`](../../../docs/plan/前端界面设计方案.md)
 * - [`docs/plan/内心OS透视架构方案.md`](../../../docs/plan/内心OS透视架构方案.md)
 */

import { ref, computed, watch } from 'vue'

// ============================================================================
// Props: 支持 GameBoard（实时）和 ReplayBoard（回放）两种来源
// ============================================================================

const props = withDefaults(defineProps<{
  /** 当前发言的玩家 ID */
  speakerId: string | null
  /** 当前发言的玩家名称 */
  speakerName?: string | null
  /** 发言内容（公开的表面发言） */
  speechContent?: string | null
  /** 内心 OS 文本（来自事件 payload.inner_thought） */
  innerThought: string | null
  /** AI 玩家的嫌疑人列表（来自 suspect_heatmap） */
  suspectList?: Record<string, number> | null
  /** 动态定位模式 */
  variant?: 'fixed' | 'seat-left' | 'seat-right' | 'speech'
}>(), {
  variant: 'fixed'
})

// ============================================================================
// 状态
// ============================================================================

/** 是否展开内心 OS 面板 */
const isExpanded = ref(false)
/** 是否正在打字机展示内心 OS */
const isTyping = ref(false)
/** 已展示的内心 OS 文本（打字机效果） */
const displayedInnerThought = ref('')
/** 内心 OS 打字机定时器 */
let typingTimer: ReturnType<typeof setInterval> | null = null

// ============================================================================
// 计算属性
// ============================================================================

/** 是否可展示内心 OS（有内心 OS 内容） */
const hasInnerThought = computed(() => {
  return props.innerThought !== null && props.innerThought !== undefined && props.innerThought.length > 0
})

/** 格式化后的嫌疑人列表 */
const suspectEntries = computed(() => {
  if (!props.suspectList) return []
  return Object.entries(props.suspectList)
    .sort(([, a], [, b]) => b - a) // 按嫌疑度从高到低排序
    .slice(0, 5) // 最多展示 5 个
})

// ============================================================================
// 打字机特效
// ============================================================================

function startTypingInnerThought(): void {
  stopTypingInnerThought()
  displayedInnerThought.value = ''
  isTyping.value = true

  const text = props.innerThought || ''
  let idx = 0
  const speed = 20 // 毫秒/字符

  typingTimer = setInterval(() => {
    if (idx >= text.length) {
      stopTypingInnerThought()
      return
    }
    idx++
    displayedInnerThought.value = text.slice(0, idx)
  }, speed)
}

function stopTypingInnerThought(): void {
  if (typingTimer !== null) {
    clearInterval(typingTimer)
    typingTimer = null
  }
  isTyping.value = false
}

/** 跳过动画，直接显示完整内容 */
function skipTyping(): void {
  stopTypingInnerThought()
  displayedInnerThought.value = props.innerThought || ''
}

// ============================================================================
// 监听：当内心 OS 或发言人变化时触发
// ============================================================================

watch(
  () => props.innerThought,
  (newThought) => {
    if (newThought) {
      startTypingInnerThought()
      isExpanded.value = true
    } else {
      stopTypingInnerThought()
      displayedInnerThought.value = ''
    }
  },
)

// 监听发言人变化，新发言人出现时自动收起上一个面板（延迟展开）
watch(
  () => props.speakerId,
  () => {
    stopTypingInnerThought()
    displayedInnerThought.value = ''
  },
)
</script>

<template>
  <div
    v-if="hasInnerThought"
    class="inner-os-panel"
    :class="[`variant-${variant}`, { expanded: isExpanded }]"
  >
    <!-- 面板头部 -->
    <div class="os-header" @click="isExpanded = !isExpanded">
      <span class="os-icon">🧠</span>
      <span class="os-title">内心 OS</span>
      <span class="os-toggle">{{ isExpanded ? '▼' : '▲' }}</span>
    </div>

    <!-- 面板内容 -->
    <div v-if="isExpanded" class="os-body">
      <!-- 发言人提示 -->
      <div class="os-speaker-hint">
        正在透视 <strong>{{ speakerName || '未知玩家' }}</strong> 的内心世界...
      </div>

      <!-- 公开表面发言（低调展示，形成反差） -->
      <div v-if="speechContent" class="os-public-speech">
        <span class="os-label-public">💬 表面发言</span>
        <div class="os-public-text">{{ speechContent }}</div>
      </div>

      <!-- 真实内心 OS（高亮展示） -->
      <div class="os-inner-section">
        <div class="os-inner-header">
          <span class="os-label-inner">🤫 真实想法</span>
          <button
            v-if="isTyping"
            class="os-skip-btn"
            @click="skipTyping"
          >
            跳过动画
          </button>
        </div>
        <div class="os-inner-text">
          {{ displayedInnerThought }}
          <span v-if="isTyping" class="os-cursor">|</span>
        </div>
      </div>

      <!-- 嫌疑人热力图（可选） -->
      <div v-if="suspectEntries.length > 0" class="os-suspect-section">
        <span class="os-label-suspect">🎯 嫌疑热力图</span>
        <div class="os-suspect-list">
          <div
            v-for="[playerId, score] in suspectEntries"
            :key="playerId"
            class="os-suspect-item"
          >
            <span class="suspect-player">{{ playerId }}</span>
            <div class="suspect-bar-bg">
              <div
                class="suspect-bar-fill"
                :style="{ width: `${Math.round(score * 100)}%` }"
              />
            </div>
            <span class="suspect-score">{{ Math.round(score * 100) }}%</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inner-os-panel {
  width: 320px;
  z-index: 1000;
  background: rgba(10, 10, 30, 0.92);
  border: 1px solid rgba(138, 43, 226, 0.4);
  border-radius: 12px;
  backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px rgba(138, 43, 226, 0.2);
  transition: all 0.3s ease;
  font-family: 'Segoe UI', system-ui, sans-serif;
}

.inner-os-panel:not(.expanded) {
  width: auto;
  min-width: 140px;
}

/* Fixed variant (fallback) */
.variant-fixed {
  position: fixed;
  right: 16px;
  top: 80px;
  max-width: calc(100vw - 32px);
}

/* Seat Left variant */
.variant-seat-left {
  position: absolute;
  left: 100%;
  margin-left: 24px;
  top: 50%;
  transform: translateY(-50%);
}

/* Seat Right variant */
.variant-seat-right {
  position: absolute;
  right: 100%;
  margin-right: 24px;
  top: 50%;
  transform: translateY(-50%);
}

/* Speech variant */
.variant-speech {
  position: absolute;
  left: 100%;
  margin-left: 24px;
  top: 0;
}

.os-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid rgba(138, 43, 226, 0.15);
}

.os-icon {
  font-size: 16px;
}

.os-title {
  font-size: 13px;
  font-weight: 700;
  color: #c084fc;
  letter-spacing: 1px;
  flex: 1;
}

.os-toggle {
  font-size: 10px;
  color: rgba(192, 132, 252, 0.5);
}

.os-body {
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
}

.os-speaker-hint {
  font-size: 12px;
  color: rgba(192, 132, 252, 0.7);
  text-align: center;
  padding-bottom: 8px;
  border-bottom: 1px dashed rgba(138, 43, 226, 0.15);
}

.os-speaker-hint strong {
  color: #c084fc;
}

.os-public-speech {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  padding: 8px 10px;
}

.os-label-public {
  font-size: 11px;
  color: #888;
  display: block;
  margin-bottom: 4px;
}

.os-public-text {
  font-size: 12px;
  color: #999;
  line-height: 1.5;
  font-style: italic;
}

.os-inner-section {
  background: rgba(138, 43, 226, 0.08);
  border: 1px solid rgba(138, 43, 226, 0.25);
  border-radius: 8px;
  padding: 8px 10px;
}

.os-inner-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.os-label-inner {
  font-size: 11px;
  font-weight: 700;
  color: #c084fc;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.os-skip-btn {
  background: rgba(138, 43, 226, 0.2);
  border: 1px solid rgba(138, 43, 226, 0.3);
  border-radius: 4px;
  color: #c084fc;
  font-size: 10px;
  padding: 2px 8px;
  cursor: pointer;
  transition: background 0.2s;
}

.os-skip-btn:hover {
  background: rgba(138, 43, 226, 0.4);
}

.os-inner-text {
  font-size: 12px;
  color: #e0d0ff;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.os-cursor {
  display: inline-block;
  animation: os-blink 1s step-end infinite;
  color: #c084fc;
  font-weight: bold;
}

@keyframes os-blink {
  50% { opacity: 0; }
}

.os-suspect-section {
  margin-top: 4px;
}

.os-label-suspect {
  font-size: 11px;
  color: #f59e0b;
  display: block;
  margin-bottom: 6px;
  font-weight: 600;
}

.os-suspect-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.os-suspect-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}

.suspect-player {
  width: 64px;
  color: #aaa;
  font-family: monospace;
  flex-shrink: 0;
}

.suspect-bar-bg {
  flex: 1;
  height: 6px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 3px;
  overflow: hidden;
}

.suspect-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #f59e0b, #ef4444);
  border-radius: 3px;
  transition: width 0.5s ease;
}

.suspect-score {
  width: 36px;
  text-align: right;
  color: #f59e0b;
  font-family: monospace;
  flex-shrink: 0;
}

/* 滚动条样式 */
.os-body::-webkit-scrollbar {
  width: 4px;
}
.os-body::-webkit-scrollbar-track {
  background: transparent;
}
.os-body::-webkit-scrollbar-thumb {
  background: rgba(138, 43, 226, 0.3);
  border-radius: 2px;
}
</style>
