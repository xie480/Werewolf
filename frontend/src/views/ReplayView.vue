<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useReplayStore } from '../store/replay'
import ReplayBoard from '../components/ReplayBoard.vue'
import ReplayTimeline from '../components/ReplayTimeline.vue'

const props = defineProps<{
  gameId: string
  perspective?: 'GOD' | 'POV'
  agentId?: string
}>()

const emit = defineEmits<{
  (e: 'back'): void
}>()

const store = useReplayStore()
const isLoading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    isLoading.value = true
    await store.fetchReplayData(props.gameId, props.perspective || 'GOD', props.agentId)
  } catch (e: any) {
    error.value = e.message || '加载回放数据失败'
  } finally {
    isLoading.value = false
  }
})

onBeforeUnmount(() => {
  store.pause()
})
</script>

<template>
  <div class="replay-view w-screen h-screen flex flex-col bg-black text-white overflow-hidden">
    <div v-if="isLoading" class="flex-1 flex items-center justify-center">
      <div class="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-purple-500"></div>
      <span class="ml-4 text-purple-400">加载回放数据中...</span>
    </div>
    
    <div v-else-if="error" class="flex-1 flex flex-col items-center justify-center gap-4">
      <div class="text-red-500 text-xl">{{ error }}</div>
      <button @click="emit('back')" class="px-4 py-2 bg-gray-800 rounded hover:bg-gray-700">返回</button>
    </div>

    <template v-else>
      <!-- 主战场区域 -->
      <div class="flex-1 relative">
        <ReplayBoard :game-id="gameId" @leave="emit('back')" />
      </div>

      <!-- 底部时间轴控制区 -->
      <div class="h-24 shrink-0 z-50">
        <ReplayTimeline />
      </div>
    </template>
  </div>
</template>
