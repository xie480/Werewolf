import { apiGet, apiPost, apiDelete } from './client';
import type { ModelConfigResponse, ModelConfigCreate } from '../types/models';

// 模拟数据，用于独立调试
let mockModels: ModelConfigResponse[] = [
  {
    id: 'gpt-4-turbo-default',
    provider: 'OpenAI',
    name: 'GPT-4 Turbo (Default)',
    base_url: 'https://api.openai.com/v1',
    model_name: 'gpt-4-turbo',
    temperature: 0.7,
    max_tokens: 2000,
    timeout: 60,
  },
  {
    id: 'claude-3-opus',
    provider: 'Anthropic',
    name: 'Claude 3 Opus',
    base_url: 'https://api.anthropic.com/v1',
    model_name: 'claude-3-opus-20240229',
    temperature: 0.5,
    max_tokens: 4000,
    timeout: 120,
  },
];

// 是否使用 Mock 数据
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true' || true; // 默认开启 Mock 以供独立调试

export const modelsApi = {
  /**
   * 获取模型列表
   */
  async getModels(): Promise<ModelConfigResponse[]> {
    if (USE_MOCK) {
      return new Promise((resolve) => {
        setTimeout(() => resolve([...mockModels]), 500);
      });
    }
    return apiGet<ModelConfigResponse[]>('/models');
  },

  /**
   * 创建/新增模型
   */
  async createModel(data: ModelConfigCreate): Promise<ModelConfigResponse> {
    if (USE_MOCK) {
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          if (mockModels.some((m) => m.id === data.id)) {
            reject(new Error('Model ID already exists'));
            return;
          }
          const newModel: ModelConfigResponse = {
            id: data.id,
            provider: data.provider,
            name: data.name,
            base_url: data.base_url,
            model_name: data.model_name,
            temperature: data.temperature,
            max_tokens: data.max_tokens,
            timeout: data.timeout,
          };
          mockModels.push(newModel);
          resolve(newModel);
        }, 500);
      });
    }
    return apiPost<ModelConfigResponse>('/models', data);
  },

  /**
   * 删除模型
   */
  async deleteModel(modelId: string): Promise<{ status: string }> {
    if (USE_MOCK) {
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          const index = mockModels.findIndex((m) => m.id === modelId);
          if (index > -1) {
            mockModels.splice(index, 1);
            resolve({ status: 'success' });
          } else {
            reject(new Error('Model not found'));
          }
        }, 500);
      });
    }
    return apiDelete<{ status: string }>(`/models/${modelId}`);
  },

  /**
   * 测试模型连通性 (模拟接口)
   */
  async testConnection(modelId: string): Promise<{ status: string; latency: number }> {
    if (USE_MOCK) {
      return new Promise((resolve, reject) => {
        setTimeout(() => {
          // 模拟 80% 成功率
          if (Math.random() > 0.2) {
            resolve({ status: 'success', latency: Math.floor(Math.random() * 1000) + 100 });
          } else {
            reject(new Error('Connection timeout or API Key invalid'));
          }
        }, 1000);
      });
    }
    // 实际后端可能需要提供 /api/models/test 接口
    return apiPost<{ status: string; latency: number }>(`/models/${modelId}/test`);
  },
};
