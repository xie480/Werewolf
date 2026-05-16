import { apiGet, apiPost, apiDelete, apiPut } from './client';
import type { ModelConfigResponse, ModelConfigCreate } from '../types/models';

export const modelsApi = {
  /**
   * 获取模型列表
   */
  async getModels(): Promise<ModelConfigResponse[]> {
    return apiGet<ModelConfigResponse[]>('/models');
  },

  /**
   * 获取单个模型配置详情
   */
  async getModel(modelId: string): Promise<ModelConfigResponse> {
    return apiGet<ModelConfigResponse>(`/models/${modelId}`);
  },

  /**
   * 创建/新增模型
   */
  async createModel(data: ModelConfigCreate): Promise<ModelConfigResponse> {
    return apiPost<ModelConfigResponse>('/models', data);
  },

  /**
   * 删除模型
   */
  async deleteModel(modelId: string): Promise<{ status: string }> {
    return apiDelete<{ status: string }>(`/models/${modelId}`);
  },

  /**
   * 更新模型（PUT）
   */
  async updateModel(data: ModelConfigCreate): Promise<ModelConfigResponse> {
    return apiPut<ModelConfigResponse>(`/models/${data.id}`, data);
  },

  /**
   * 测试模型连通性
   */
  async testConnection(modelId: string): Promise<{ status: string; latency: number }> {
    return apiPost<{ status: string; latency: number }>(`/models/${modelId}/test`);
  },
};
