<template>
  <div class="min-h-screen bg-gray-50 p-6">
    <div class="max-w-7xl mx-auto">
      <!-- Header -->
      <div class="flex justify-between items-center mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">模型管理控制台</h1>
          <p class="text-sm text-gray-500 mt-1">管理和配置 AI 狼人杀系统使用的各类大语言模型</p>
        </div>
        <button
          @click="openCreateForm"
          class="px-4 py-2 bg-blue-600 text-white rounded-lg shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 flex items-center gap-2 transition-colors"
        >
          <Plus class="w-5 h-5" />
          新增模型
        </button>
      </div>

      <!-- Global Error Alert -->
      <div v-if="modelStore.error" class="mb-6 p-4 bg-red-50 border-l-4 border-red-500 rounded-r-md flex items-start">
        <AlertCircle class="w-5 h-5 text-red-500 mt-0.5 mr-3 flex-shrink-0" />
        <div class="flex-1">
          <h3 class="text-sm font-medium text-red-800">操作失败</h3>
          <p class="mt-1 text-sm text-red-700">{{ modelStore.error }}</p>
        </div>
        <button @click="modelStore.error = null" class="text-red-400 hover:text-red-600">
          <X class="w-5 h-5" />
        </button>
      </div>

      <!-- Main Content: Model List -->
      <ModelList
        :models="modelStore.models"
        :is-loading="modelStore.isLoading"
        :test-status="modelStore.testStatus"
        @edit="openEditForm"
        @delete="handleDelete"
        @test="handleTest"
      />

      <!-- Drawer: Model Config Form -->
      <Transition name="fade">
        <ModelConfigForm
          v-if="isFormVisible"
          v-model="isFormVisible"
          :initial-data="editingModel"
          :is-saving="modelStore.isSaving"
          @submit="handleFormSubmit"
        />
      </Transition>

      <!-- Toast Notification (Simple implementation) -->
      <Transition name="slide-up">
        <div v-if="toast.visible" class="fixed bottom-4 right-4 z-50">
          <div 
            class="px-4 py-3 rounded-lg shadow-lg flex items-center gap-3"
            :class="toast.type === 'success' ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-red-50 text-red-800 border border-red-200'"
          >
            <CheckCircle2 v-if="toast.type === 'success'" class="w-5 h-5 text-green-500" />
            <AlertCircle v-else class="w-5 h-5 text-red-500" />
            <span class="text-sm font-medium">{{ toast.message }}</span>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue';
import { Plus, AlertCircle, X, CheckCircle2 } from 'lucide-vue-next';
import { useModelStore } from '../store/models';
import ModelList from '../components/models/ModelList.vue';
import ModelConfigForm from '../components/models/ModelConfigForm.vue';
import type { ModelConfigResponse, ModelConfigCreate } from '../types/models';

const modelStore = useModelStore();

// 状态
const isFormVisible = ref(false);
const editingModel = ref<ModelConfigResponse | null>(null);

// Toast 状态
const toast = reactive({
  visible: false,
  message: '',
  type: 'success' as 'success' | 'error',
});

const showToast = (message: string, type: 'success' | 'error' = 'success') => {
  toast.message = message;
  toast.type = type;
  toast.visible = true;
  setTimeout(() => {
    toast.visible = false;
  }, 3000);
};

// 初始化加载
onMounted(() => {
  modelStore.fetchModels();
});

// 交互处理
const openCreateForm = () => {
  editingModel.value = null;
  isFormVisible.value = true;
};

const openEditForm = (model: ModelConfigResponse) => {
  editingModel.value = model;
  isFormVisible.value = true;
};

const handleDelete = async (modelId: string) => {
  try {
    await modelStore.deleteModel(modelId);
    showToast('模型删除成功');
  } catch (error) {
    // 错误已在 store 中处理并显示在全局 Alert 中
  }
};

const handleTest = async (modelId: string) => {
  try {
    const result = await modelStore.testConnection(modelId);
    showToast(`连接成功，延迟: ${result.latency}ms`);
  } catch (error: any) {
    showToast(error.message || '连接测试失败', 'error');
  }
};

const handleFormSubmit = async (data: ModelConfigCreate, testAfterSave: boolean) => {
  try {
    if (editingModel.value) {
      // 编辑逻辑：使用 PUT 更新模型
      await modelStore.updateModel(data);
      showToast('模型更新成功');
    } else {
      // 新增逻辑
      await modelStore.createModel(data);
      showToast('模型创建成功');
    }
    
    isFormVisible.value = false;

    if (testAfterSave) {
      await handleTest(data.id);
    }
  } catch (error) {
    // 错误已在 store 中处理并显示在全局 Alert 中
  }
};
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.slide-up-enter-active,
.slide-up-leave-active {
  transition: all 0.3s ease;
}

.slide-up-enter-from,
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(20px);
}
</style>
