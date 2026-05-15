<template>
  <div class="fixed inset-0 z-50 flex justify-end bg-black/50 transition-opacity" @click.self="handleClose">
    <div class="w-full max-w-md bg-white h-full shadow-2xl flex flex-col transform transition-transform duration-300 translate-x-0">
      <!-- Header -->
      <div class="px-6 py-4 border-b border-gray-200 flex justify-between items-center bg-gray-50">
        <h2 class="text-xl font-semibold text-gray-800">
          {{ isEdit ? '编辑模型配置' : '新增模型配置' }}
        </h2>
        <button @click="handleClose" class="text-gray-400 hover:text-gray-600 transition-colors">
          <X class="w-6 h-6" />
        </button>
      </div>

      <!-- Form Body -->
      <div class="flex-1 overflow-y-auto p-6">
        <form @submit.prevent="handleSubmit" class="space-y-5">
          
          <!-- 基础信息 -->
          <div class="space-y-4">
            <h3 class="text-sm font-medium text-gray-900 border-b pb-2">基础信息</h3>
            
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">模型 ID <span class="text-red-500">*</span></label>
              <input 
                v-model="formData.id" 
                type="text" 
                required
                :disabled="isEdit"
                class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100 disabled:text-gray-500 text-gray-900 sm:text-sm"
                placeholder="例如: gpt-4-default"
              />
              <p v-if="isEdit" class="mt-1 text-xs text-gray-500">编辑模式下不可修改 ID</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">显示名称 <span class="text-red-500">*</span></label>
              <input 
                v-model="formData.name" 
                type="text" 
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                placeholder="例如: GPT-4 Turbo"
              />
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">供应商 <span class="text-red-500">*</span></label>
              <select 
                v-model="formData.provider"
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm bg-white"
              >
                <option value="OpenAI">OpenAI</option>
                <option value="Anthropic">Anthropic</option>
                <option value="Local">Local / Ollama</option>
                <option value="Other">Other</option>
              </select>
            </div>
          </div>

          <!-- 连接配置 -->
          <div class="space-y-4 pt-2">
            <h3 class="text-sm font-medium text-gray-900 border-b pb-2">连接配置</h3>
            
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">Base URL <span class="text-red-500">*</span></label>
              <input 
                v-model="formData.base_url" 
                type="url" 
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                placeholder="https://api.openai.com/v1"
              />
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">API Key <span v-if="!isEdit" class="text-red-500">*</span></label>
              <div class="relative">
                <input 
                  v-model="formData.api_key" 
                  :type="showApiKey ? 'text' : 'password'" 
                  :required="!isEdit"
                  class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm pr-10"
                  :placeholder="isEdit ? '•••••••••••••••• (留空表示不修改)' : 'sk-...'"
                />
                <button 
                  type="button"
                  @click="showApiKey = !showApiKey"
                  class="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-600"
                >
                  <Eye v-if="!showApiKey" class="w-4 h-4" />
                  <EyeOff v-else class="w-4 h-4" />
                </button>
              </div>
              <p v-if="isEdit" class="mt-1 text-xs text-amber-600">
                出于安全原因，API Key 不予显示。若需修改，请重新输入；若留空，则保持原 Key 不变。
              </p>
            </div>
          </div>

          <!-- 推理参数 -->
          <div class="space-y-4 pt-2">
            <h3 class="text-sm font-medium text-gray-900 border-b pb-2">推理参数</h3>
            
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">Model Name <span class="text-red-500">*</span></label>
              <input 
                v-model="formData.model_name" 
                type="text" 
                required
                class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                placeholder="例如: gpt-4-turbo"
              />
            </div>

            <div>
              <div class="flex justify-between mb-1">
                <label class="block text-sm font-medium text-gray-700">Temperature</label>
                <span class="text-sm text-gray-500">{{ formData.temperature }}</span>
              </div>
              <input 
                v-model.number="formData.temperature" 
                type="range" 
                min="0" max="2" step="0.1"
                class="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
              />
            </div>

            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                <input 
                  v-model.number="formData.max_tokens" 
                  type="number" 
                  min="1"
                  class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                />
              </div>
              <div>
                <label class="block text-sm font-medium text-gray-700 mb-1">Timeout (秒)</label>
                <input 
                  v-model.number="formData.timeout" 
                  type="number" 
                  min="1"
                  class="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                />
              </div>
            </div>
          </div>

        </form>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-gray-200 bg-gray-50 flex justify-end gap-3">
        <button 
          type="button" 
          @click="handleClose"
          class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
        >
          取消
        </button>
        <button 
          type="button" 
          @click="submitForm(false)"
          :disabled="isSaving"
          class="px-4 py-2 text-sm font-medium text-white bg-blue-600 border border-transparent rounded-md shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 flex items-center"
        >
          <Loader2 v-if="isSaving" class="w-4 h-4 mr-2 animate-spin" />
          仅保存
        </button>
        <button 
          type="button" 
          @click="submitForm(true)"
          :disabled="isSaving"
          class="px-4 py-2 text-sm font-medium text-white bg-green-600 border border-transparent rounded-md shadow-sm hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 disabled:opacity-50 flex items-center"
        >
          <Loader2 v-if="isSaving" class="w-4 h-4 mr-2 animate-spin" />
          保存并测试
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue';
import { X, Eye, EyeOff, Loader2 } from 'lucide-vue-next';
import type { ModelConfigResponse, ModelConfigCreate } from '../../types/models';

const props = defineProps<{
  modelValue: boolean; // 控制抽屉显示隐藏
  initialData?: ModelConfigResponse | null;
  isSaving: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'submit', data: ModelConfigCreate, testAfterSave: boolean): void;
}>();

const isEdit = ref(false);
const showApiKey = ref(false);

const defaultFormData: ModelConfigCreate = {
  id: '',
  provider: 'OpenAI',
  name: '',
  api_key: '',
  base_url: '',
  model_name: '',
  temperature: 0.7,
  max_tokens: 2000,
  timeout: 60,
};

const formData = reactive<ModelConfigCreate>({ ...defaultFormData });

// 监听初始数据变化，用于编辑模式回显
watch(() => props.initialData, (newVal) => {
  if (newVal) {
    isEdit.value = true;
    Object.assign(formData, {
      ...newVal,
      api_key: '', // 编辑时 API Key 留空
    });
  } else {
    isEdit.value = false;
    Object.assign(formData, { ...defaultFormData });
  }
}, { immediate: true });

const handleClose = () => {
  emit('update:modelValue', false);
};

const submitForm = (testAfterSave: boolean) => {
  // 简单表单校验
  if (!formData.id || !formData.name || !formData.provider || !formData.base_url || !formData.model_name) {
    alert('请填写所有必填项');
    return;
  }
  if (!isEdit.value && !formData.api_key) {
    alert('新增模型必须填写 API Key');
    return;
  }
  
  emit('submit', { ...formData }, testAfterSave);
};

const handleSubmit = () => {
  submitForm(false);
};
</script>
