import { defineStore } from 'pinia';
import { ref } from 'vue';
import type { ModelConfigResponse, ModelConfigCreate, TestStatus } from '../types/models';
import { modelsApi } from '../api/models';

export const useModelStore = defineStore('models', () => {
  // 状态
  const models = ref<ModelConfigResponse[]>([]);
  const isLoading = ref(false);
  const isSaving = ref(false);
  const testStatus = ref<Record<string, TestStatus>>({});
  const error = ref<string | null>(null);

  // 动作
  const fetchModels = async () => {
    isLoading.value = true;
    error.value = null;
    try {
      const data = await modelsApi.getModels();
      models.value = data;
    } catch (err: any) {
      error.value = err.message || '获取模型列表失败';
      console.error('Failed to fetch models:', err);
    } finally {
      isLoading.value = false;
    }
  };

  const createModel = async (data: ModelConfigCreate) => {
    isSaving.value = true;
    error.value = null;
    try {
      const newModel = await modelsApi.createModel(data);
      models.value.push(newModel);
      return newModel;
    } catch (err: any) {
      error.value = err.message || '创建模型失败';
      console.error('Failed to create model:', err);
      throw err;
    } finally {
      isSaving.value = false;
    }
  };

  const deleteModel = async (modelId: string) => {
    error.value = null;
    try {
      await modelsApi.deleteModel(modelId);
      models.value = models.value.filter((m) => m.id !== modelId);
    } catch (err: any) {
      error.value = err.message || '删除模型失败';
      console.error('Failed to delete model:', err);
      throw err;
    }
  };

  const testConnection = async (modelId: string) => {
    testStatus.value[modelId] = 'testing';
    try {
      const result = await modelsApi.testConnection(modelId);
      testStatus.value[modelId] = 'success';
      return result;
    } catch (err: any) {
      testStatus.value[modelId] = 'error';
      console.error(`Failed to test connection for ${modelId}:`, err);
      throw err;
    }
  };

  return {
    models,
    isLoading,
    isSaving,
    testStatus,
    error,
    fetchModels,
    createModel,
    deleteModel,
    testConnection,
  };
});
