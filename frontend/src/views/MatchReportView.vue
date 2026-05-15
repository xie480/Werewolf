<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getGameReport } from '../api/report'
import type { MatchReportResponse } from '../types/api'
import ReportHeader from '../components/report/ReportHeader.vue'
import PlayerEvalCard from '../components/report/PlayerEvalCard.vue'
import WinRateChart from '../components/report/WinRateChart.vue'

const props = defineProps<{
  gameId: string
}>()

const emit = defineEmits<{
  (e: 'back'): void
}>()

const loading = ref(true)
const error = ref<string | null>(null)
const reportData = ref<MatchReportResponse | null>(null)

async function fetchReport() {
  loading.value = true
  error.value = null
  try {
    reportData.value = await getGameReport(props.gameId)
  } catch (err: any) {
    if (err.response?.status === 404) {
      error.value = '复盘报告正在生成中，请稍候再试...'
    } else {
      error.value = err.response?.data?.detail || err.message || '获取复盘报告失败'
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchReport()
})
</script>

<template>
  <div class="match-report-view min-h-screen bg-[#111] text-gray-200 p-6 md:p-12 overflow-y-auto">
    <div class="max-w-7xl mx-auto">
      <!-- 顶部导航 -->
      <div class="mb-8 flex items-center justify-between">
        <button 
          @click="emit('back')"
          class="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors text-sm"
        >
          <span>←</span> 返回大厅
        </button>
        <button 
          @click="fetchReport"
          class="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors text-sm"
          :disabled="loading"
        >
          <span>↻</span> 刷新
        </button>
      </div>

      <!-- 加载态 -->
      <div v-if="loading" class="flex flex-col items-center justify-center py-32 gap-4">
        <div class="w-12 h-12 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
        <div class="text-gray-400">正在加载复盘报告...</div>
      </div>

      <!-- 错误态 -->
      <div v-else-if="error" class="flex flex-col items-center justify-center py-32 gap-6">
        <div class="text-6xl">📄</div>
        <div class="text-xl text-gray-300">{{ error }}</div>
        <button 
          @click="fetchReport"
          class="px-6 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors"
        >
          重试
        </button>
      </div>

      <!-- 报告内容 -->
      <div v-else-if="reportData" class="report-content animate-fade-in">
        <ReportHeader
          :game-id="reportData.game_id"
          :winner="reportData.winner"
          :duration-seconds="reportData.duration_seconds"
          :mvp-agent-id="reportData.mvp_agent_id"
        />

        <WinRateChart
          v-if="reportData.faction_win_probability_curve"
          :curve-data="reportData.faction_win_probability_curve"
        />

        <div class="mb-6">
          <h2 class="text-2xl font-bold text-white mb-2">玩家评测详情</h2>
          <p class="text-gray-400 text-sm">基于 LLM 裁判与启发式规则的五维评分及详细评价</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          <PlayerEvalCard 
            v-for="evalData in reportData.evaluations" 
            :key="evalData.player_id"
            :evaluation="evalData"
            :is-mvp="evalData.player_id === reportData.mvp_agent_id"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.animate-fade-in {
  animation: fadeIn 0.5s ease-out;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
