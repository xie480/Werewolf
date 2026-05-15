<template>
  <div class="model-list">
    <div v-if="isLoading" class="loading-state">
      <Loader2 class="animate-spin w-8 h-8 text-blue-500" />
      <span class="ml-2 text-gray-600">加载模型列表中...</span>
    </div>

    <div v-else-if="models.length === 0" class="empty-state">
      <Database class="w-12 h-12 text-gray-400 mb-4" />
      <h3 class="text-lg font-medium text-gray-900">暂无模型配置</h3>
      <p class="text-gray-500 mt-1">点击右上角“新增模型”开始配置</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      <div
        v-for="model in models"
        :key="model.id"
        class="model-card bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden hover:shadow-md transition-shadow duration-200"
      >
        <!-- Card Header -->
        <div class="p-5 border-b border-gray-100 flex justify-between items-start">
          <div>
            <div class="flex items-center gap-2 mb-1">
              <h3 class="text-lg font-semibold text-gray-900 truncate" :title="model.name">
                {{ model.name }}
              </h3>
              <span
                class="px-2 py-0.5 text-xs font-medium rounded-full"
                :class="getProviderColor(model.provider)"
              >
                {{ model.provider }}
              </span>
            </div>
            <p class="text-sm text-gray-500 font-mono truncate" :title="model.id">
              {{ model.id }}
            </p>
          </div>
          <div class="flex gap-1">
            <button
              @click="$emit('edit', model)"
              class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded-md transition-colors"
              title="编辑"
            >
              <Edit2 class="w-4 h-4" />
            </button>
            <button
              @click="confirmDelete(model)"
              class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md transition-colors"
              title="删除"
            >
              <Trash2 class="w-4 h-4" />
            </button>
          </div>
        </div>

        <!-- Card Body -->
        <div class="p-5 space-y-3">
          <div class="flex justify-between text-sm">
            <span class="text-gray-500">模型名称:</span>
            <span class="font-medium text-gray-900 truncate max-w-[150px]" :title="model.model_name">
              {{ model.model_name }}
            </span>
          </div>
          <div class="flex justify-between text-sm">
            <span class="text-gray-500">Temperature:</span>
            <span class="font-medium text-gray-900">{{ model.temperature }}</span>
          </div>
          <div class="flex justify-between text-sm">
            <span class="text-gray-500">Max Tokens:</span>
            <span class="font-medium text-gray-900">{{ model.max_tokens }}</span>
          </div>
        </div>

        <!-- Card Footer -->
        <div class="px-5 py-3 bg-gray-50 border-t border-gray-100 flex justify-between items-center">
          <div class="flex items-center gap-2">
            <div
              class="w-2 h-2 rounded-full"
              :class="getStatusColor(testStatus[model.id])"
            ></div>
            <span class="text-xs text-gray-600">
              {{ getStatusText(testStatus[model.id]) }}
            </span>
          </div>
          <button
            @click="handleTest(model.id)"
            :disabled="testStatus[model.id] === 'testing'"
            class="text-sm font-medium text-blue-600 hover:text-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
          >
            <Activity class="w-4 h-4" v-if="testStatus[model.id] !== 'testing'" />
            <Loader2 class="w-4 h-4 animate-spin" v-else />
            测试连接
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Edit2, Trash2, Activity, Loader2, Database } from 'lucide-vue-next';
import type { ModelConfigResponse, TestStatus } from '../../types/models';

const props = defineProps<{
  models: ModelConfigResponse[];
  isLoading: boolean;
  testStatus: Record<string, TestStatus>;
}>();

const emit = defineEmits<{
  (e: 'edit', model: ModelConfigResponse): void;
  (e: 'delete', modelId: string): void;
  (e: 'test', modelId: string): void;
}>();

// 供应商标签颜色映射
const getProviderColor = (provider: string) => {
  const colors: Record<string, string> = {
    OpenAI: 'bg-green-100 text-green-800',
    Anthropic: 'bg-purple-100 text-purple-800',
    Local: 'bg-gray-100 text-gray-800',
  };
  return colors[provider] || 'bg-blue-100 text-blue-800';
};

// 测试状态颜色映射
const getStatusColor = (status?: TestStatus) => {
  switch (status) {
    case 'success': return 'bg-green-500';
    case 'error': return 'bg-red-500';
    case 'testing': return 'bg-yellow-500 animate-pulse';
    default: return 'bg-gray-300';
  }
};

// 测试状态文本映射
const getStatusText = (status?: TestStatus) => {
  switch (status) {
    case 'success': return '连接成功';
    case 'error': return '连接失败';
    case 'testing': return '测试中...';
    default: return '未测试';
  }
};

const confirmDelete = (model: ModelConfigResponse) => {
  if (confirm(`确定要删除模型配置 "${model.name}" 吗？此操作不可恢复。`)) {
    emit('delete', model.id);
  }
};

const handleTest = (modelId: string) => {
  emit('test', modelId);
};
</script>

<style scoped>
.model-list {
  width: 100%;
}
.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding-top: 5rem;
  padding-bottom: 5rem;
}
</style>
