<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useReplayStore } from '../store/replay'

const store = useReplayStore()

const currentThought = computed(() => {
  return store.currentGameState.innerThoughts[store.currentSeqId] || ''
})

// 打字机效果状态
const displayedThought = ref('')
let typeInterval: number | null = null

watch(currentThought, (newThought) => {
  if (typeInterval) {
    clearInterval(typeInterval)
    typeInterval = null
  }
  
  if (!newThought) {
    displayedThought.value = ''
    return
  }

  // 如果倍速很高，直接显示
  if (store.playbackSpeed >= 4) {
    displayedThought.value = newThought
    return
  }

  displayedThought.value = ''
  let i = 0
  // 根据倍速调整打字速度
  const speed = Math.max(10, 30 / store.playbackSpeed)
  
  typeInterval = window.setInterval(() => {
    if (i < newThought.length) {
      displayedThought.value += newThought.charAt(i)
      i++
    } else {
      if (typeInterval) clearInterval(typeInterval)
    }
  }, speed)
})
</script>

<template>
  <div 
    v-if="currentThought"
    class="absolute right-4 top-20 w-80 bg-black/80 backdrop-blur-md border border-purple-500/30 rounded-lg shadow-2xl shadow-purple-900/20 overflow-hidden z-40 flex flex-col"
  >
    <div class="bg-purple-900/40 px-4 py-2 border-b border-purple-500/30 flex items-center gap-2">
      <div class="w-2 h-2 rounded-full bg-purple-400 animate-pulse"></div>
      <span class="text-purple-200 text-sm font-medium tracking-wider">INNER OS / 内部推理</span>
    </div>
    <div class="p-4 text-sm text-purple-100/90 leading-relaxed font-mono whitespace-pre-wrap max-h-96 overflow-y-auto">
      {{ displayedThought }}<span class="animate-pulse">_</span>
    </div>
  </div>
</template>
