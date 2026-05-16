<script setup lang="ts">
/**
 * AI 聊天气泡组件 —— 打字机特效逐字显示发言内容。
 *
 * **Why**: 设计中要求 AI 发言使用打字机特效，一行行展示 AI 的推理过程。
 * 同时提供 [跳过动画] 按钮，点击直接显示完整内容。
 */

import { ref, watch, onBeforeUnmount } from 'vue'

const props = defineProps<{
  /** 发言内容 */
  content: string
  /** 发言人 ID */
  speakerId: string
  /** 发言人可读名称（优先显示，无则回退显示 speakerId） */
  speakerName?: string
  /** 打字速度（毫秒/字符），默认 30ms */
  speed?: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

/** 当前已显示的文字 */
const displayedText = ref('')
/** 是否已完成打字 */
const isDone = ref(false)
/** 打字定时器 */
let timer: ReturnType<typeof setInterval> | null = null
/** 当前字符索引 */
let currentIndex = 0

/** 开始打字特效 */
function startTyping(): void {
  stopTyping()
  currentIndex = 0
  displayedText.value = ''
  isDone.value = false

  const speed = props.speed ?? 30
  const text = props.content

  timer = setInterval(() => {
    if (currentIndex >= text.length) {
      stopTyping()
      isDone.value = true
      emit('done')
      return
    }
    currentIndex++
    displayedText.value = text.slice(0, currentIndex)
  }, speed)
}

/** 停止打字特效 */
function stopTyping(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

/** 跳过动画，直接显示完整内容 */
function skip(): void {
  stopTyping()
  displayedText.value = props.content
  isDone.value = true
  emit('done')
}

/** 监听 content 变化，重新开始打字 */
watch(
  () => props.content,
  (newContent) => {
    if (newContent) {
      startTyping()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopTyping()
})
</script>

<template>
  <div class="speech-bubble">
    <div class="speech-header">
      <span class="speaker-name">{{ speakerName || speakerId }}</span>
      <button
        v-if="!isDone"
        class="skip-btn"
        @click="skip"
      >
        跳过动画
      </button>
    </div>
    <div class="speech-content">
      {{ displayedText }}
      <span v-if="!isDone" class="cursor">|</span>
    </div>
  </div>
</template>

<style scoped>
.speech-bubble {
  background: rgba(20, 20, 40, 0.9);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  padding: 12px 16px;
  max-width: 480px;
  min-width: 200px;
  backdrop-filter: blur(8px);
}

.speech-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.speaker-name {
  font-size: 13px;
  font-weight: 600;
  color: #ffd700;
}

.skip-btn {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 6px;
  color: #aaa;
  font-size: 11px;
  padding: 2px 10px;
  cursor: pointer;
  transition: background 0.2s;
}

.skip-btn:hover {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.speech-content {
  font-size: 14px;
  line-height: 1.6;
  color: #e0e0e0;
  white-space: pre-wrap;
  word-break: break-word;
}

.cursor {
  display: inline-block;
  animation: blink 1s step-end infinite;
  color: #ffd700;
  font-weight: bold;
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}
</style>
