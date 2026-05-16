/**
 * 模型配置响应接口
 * 注意：响应中不包含 api_key，这是后端的安全设计
 *
 * **修改说明**: 删除了重复的 `name` 字段，统一使用 `model_name` 作为唯一的模型名称标识。
 */
export interface ModelConfigResponse {
  id: string;
  provider: string;
  base_url: string;
  model_name: string;
  temperature: number;
  max_tokens: number;
  timeout: number;
}

/**
 * 创建/新增模型配置请求接口
 *
 * **修改说明**: 删除了 `name` 和 `api_key` 字段。
 */
export interface ModelConfigCreate {
  id: string;
  provider: string;
  api_key: string;
  base_url: string;
  model_name: string;
  temperature: number;
  max_tokens: number;
  timeout: number;
}

/**
 * 模型连通性测试状态
 */
export type TestStatus = 'idle' | 'testing' | 'success' | 'error';
