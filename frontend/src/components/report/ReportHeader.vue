<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  gameId: string
  winner: string
  durationSeconds: number
  mvpAgentId: string
}>()

const formattedDuration = computed(() => {
  const m = Math.floor(props.durationSeconds / 60)
  const s = props.durationSeconds % 60
  return `${m}分${s}秒`
})

const winnerLabel = computed(() => {
  if (props.winner === 'VILLAGER') return '好人阵营胜利'
  if (props.winner === 'WEREWOLF') return '狼人阵营胜利'
  return props.winner
})

const winnerClass = computed(() => {
  if (props.winner === 'VILLAGER') return 'text-blue-400'
  if (props.winner === 'WEREWOLF') return 'text-red-500'
  return 'text-gray-300'
})
</script>

<template>
  <div class="report-header bg-gray-800/80 border border-gray-700 rounded-xl p-6 mb-8 backdrop-blur-sm flex flex-col md:flex-row items-center justify-between gap-6">
    <div class="flex flex-col items-center md:items-start gap-2">
      <h1 class="text-3xl font-bold" :class="winnerClass">{{ winnerLabel }}</h1>
      <div class="text-gray-400 text-sm flex gap-4">
        <span>对局 ID: <span class="font-mono text-gray-300">{{ gameId }}</span></span>
        <span>时长: <span class="text-gray-300">{{ formattedDuration }}</span></span>
      </div>
    </div>
    
    <div class="mvp-badge flex items-center gap-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
      <div class="text-yellow-500 font-bold text-xl">MVP</div>
      <div class="flex flex-col">
        <span class="text-white font-medium">{{ mvpAgentId }}</span>
        <span class="text-yellow-500/80 text-xs">全场最佳表现</span>
      </div>
    </div>
  </div>
</template>
