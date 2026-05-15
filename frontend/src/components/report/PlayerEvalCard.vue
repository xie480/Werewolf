<script setup lang="ts">
import { computed } from 'vue'
import type { AgentEvaluationResponse } from '../../types/api'
import RadarChart from './RadarChart.vue'

const props = defineProps<{
  evaluation: AgentEvaluationResponse
  isMvp: boolean
}>()

const roleLabel = computed(() => {
  const map: Record<string, string> = {
    VILLAGER: '平民',
    WEREWOLF: '狼人',
    SEER: '预言家',
    WITCH: '女巫',
    HUNTER: '猎人'
  }
  return map[props.evaluation.role] || props.evaluation.role
})

const roleColor = computed(() => {
  if (props.evaluation.role === 'WEREWOLF') return 'text-red-400 border-red-400/30 bg-red-400/10'
  if (['SEER', 'WITCH', 'HUNTER'].includes(props.evaluation.role)) return 'text-purple-400 border-purple-400/30 bg-purple-400/10'
  return 'text-blue-400 border-blue-400/30 bg-blue-400/10'
})
</script>

<template>
  <div class="eval-card bg-gray-800/50 border border-gray-700 rounded-xl overflow-hidden flex flex-col relative">
    <div v-if="isMvp" class="absolute top-0 right-0 bg-yellow-500 text-black text-xs font-bold px-3 py-1 rounded-bl-lg z-10">
      MVP
    </div>
    
    <div class="p-4 border-b border-gray-700 flex justify-between items-center bg-gray-800/80">
      <div class="font-bold text-lg text-white">{{ evaluation.player_id }}</div>
      <div class="px-2 py-1 rounded text-xs border" :class="roleColor">
        {{ roleLabel }}
      </div>
    </div>
    
    <div class="p-4 flex-1 flex flex-col gap-4">
      <RadarChart :evaluation="evaluation" />
      
      <div class="eval-text flex flex-col gap-3 text-sm">
        <div v-if="evaluation.strengths" class="bg-green-900/20 border border-green-800/50 rounded p-3">
          <div class="text-green-400 font-bold mb-1 flex items-center gap-1">
            <span class="text-lg leading-none">✦</span> 高光时刻
          </div>
          <div class="text-gray-300 leading-relaxed">{{ evaluation.strengths }}</div>
        </div>
        
        <div v-if="evaluation.weaknesses" class="bg-red-900/20 border border-red-800/50 rounded p-3">
          <div class="text-red-400 font-bold mb-1 flex items-center gap-1">
            <span class="text-lg leading-none">⚠</span> 致命失误
          </div>
          <div class="text-gray-300 leading-relaxed">{{ evaluation.weaknesses }}</div>
        </div>
        
        <div v-if="evaluation.overall_review" class="bg-blue-900/20 border border-blue-800/50 rounded p-3">
          <div class="text-blue-400 font-bold mb-1 flex items-center gap-1">
            <span class="text-lg leading-none">📝</span> 综合评价
          </div>
          <div class="text-gray-300 leading-relaxed">{{ evaluation.overall_review }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
