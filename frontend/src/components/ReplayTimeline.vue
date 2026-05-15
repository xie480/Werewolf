<script setup lang="ts">
import { computed } from 'vue'
import { useReplayStore } from '../store/replay'
import { Play, Pause, FastForward, SkipBack, SkipForward } from 'lucide-vue-next'

const store = useReplayStore()

const progress = computed(() => {
  if (store.maxSeqId === 0) return 0
  return (store.currentSeqId / store.maxSeqId) * 100
})

function handleSeek(event: MouseEvent) {
  const target = event.currentTarget as HTMLElement
  const rect = target.getBoundingClientRect()
  const x = event.clientX - rect.left
  const percentage = x / rect.width
  const targetSeqId = Math.round(percentage * store.maxSeqId)
  store.seek(targetSeqId)
}

const speeds = [1, 2, 4]

function toggleSpeed() {
  const currentIndex = speeds.indexOf(store.playbackSpeed)
  const nextIndex = (currentIndex + 1) % speeds.length
  store.setSpeed(speeds[nextIndex])
}
</script>

<template>
  <div class="bg-gray-900/80 backdrop-blur-md border-t border-gray-800 p-4 flex flex-col gap-2">
    <!-- Timeline Bar -->
    <div 
      class="h-2 bg-gray-700 rounded-full cursor-pointer relative overflow-hidden"
      @click="handleSeek"
    >
      <div 
        class="absolute top-0 left-0 h-full bg-blue-500 transition-all duration-200 ease-linear"
        :style="{ width: `${progress}%` }"
      ></div>
    </div>

    <!-- Controls -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-4">
        <button 
          @click="store.seek(0)"
          class="p-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-full transition-colors"
          title="Restart"
        >
          <SkipBack class="w-5 h-5" />
        </button>
        
        <button 
          @click="store.togglePlay"
          class="p-3 bg-blue-600 text-white hover:bg-blue-500 rounded-full transition-colors shadow-lg shadow-blue-900/20"
        >
          <Pause v-if="store.isPlaying" class="w-6 h-6" />
          <Play v-else class="w-6 h-6 ml-1" />
        </button>

        <button 
          @click="store.stepForward"
          class="p-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-full transition-colors"
          title="Next Event"
        >
          <SkipForward class="w-5 h-5" />
        </button>

        <button 
          @click="toggleSpeed"
          class="px-3 py-1.5 text-sm font-medium text-blue-400 bg-blue-900/30 hover:bg-blue-800/40 rounded-md transition-colors flex items-center gap-1"
        >
          <FastForward class="w-4 h-4" />
          {{ store.playbackSpeed }}x
        </button>
      </div>

      <div class="text-sm text-gray-400 font-mono">
        Seq: {{ store.currentSeqId }} / {{ store.maxSeqId }}
      </div>
    </div>
  </div>
</template>
