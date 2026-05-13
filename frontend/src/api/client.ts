/**
 * HTTP 客户端封装 —— 基于原生 fetch 的轻量请求层。
 *
 * **Why**: 不引入 axios 依赖，利用 Vite 已配置的 /api 代理转发到后端。
 * 仅封装 GET/POST 通用逻辑和统一错误处理，保持与项目 tech stack 最小化原则一致。
 *
 * 参考:
 * - [`../types/api.ts`](../types/api.ts)
 * - [`../../vite.config.ts`](../../vite.config.ts) 中的 proxy 配置
 */


// ============================================================================
// 配置常量
// ============================================================================

/** API 基础路径——Vite 代理将 /api/* 转发到 http://localhost:8000 */
const API_BASE = '/api'

/** 默认请求超时时间（毫秒） */
const DEFAULT_TIMEOUT_MS = 30_000


// ============================================================================
// 自定义错误类型
// ============================================================================

/** API 请求失败错误——携带 HTTP 状态码和后端错误详情 */
export class ApiError extends Error {
  /** HTTP 状态码 */
  status: number
  /** 后端返回的错误详情（如有） */
  detail?: string

  constructor(status: number, message: string, detail?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}


// ============================================================================
// 通用请求函数
// ============================================================================

/**
 * 带有超时控制的 fetch 封装。
 *
 * @param url 请求 URL（已拼接 API_BASE 前缀）
 * @param options fetch 选项
 * @param timeoutMs 超时毫秒数
 * @returns fetch Response
 * @throws ApiError 网络错误、超时或非 2xx 响应
 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })

    if (!response.ok) {
      let detail: string | undefined
      try {
        const errorBody = await response.json()
        detail = errorBody.detail ?? errorBody.error
      } catch {
        // 响应体不是 JSON，忽略
      }
      throw new ApiError(
        response.status,
        `请求失败: ${response.status} ${response.statusText}`,
        detail,
      )
    }

    return response
  } catch (err) {
    if (err instanceof ApiError) {
      throw err
    }
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(0, `请求超时 (${timeoutMs}ms)`)
    }
    throw new ApiError(0, `网络错误: ${(err as Error).message}`)
  } finally {
    clearTimeout(timeoutId)
  }
}


// ============================================================================
// 导出的请求方法
// ============================================================================

/**
 * 发送 GET 请求并解析 JSON 响应体。
 *
 * @param path API 路径（如 "/games"），自动拼接 /api 前缀
 * @param params 可选的 URL 查询参数
 * @returns 解析后的 JSON 响应体
 * @throws ApiError 请求失败时抛出
 */
export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  let url = `${API_BASE}${path}`
  if (params) {
    const searchParams = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        searchParams.set(key, String(value))
      }
    }
    const qs = searchParams.toString()
    if (qs) {
      url += `?${qs}`
    }
  }

  const response = await fetchWithTimeout(url, { method: 'GET' })
  return response.json() as Promise<T>
}

/**
 * 发送 POST 请求并解析 JSON 响应体。
 *
 * @param path API 路径（如 "/games"），自动拼接 /api 前缀
 * @param body 请求体对象，将序列化为 JSON
 * @returns 解析后的 JSON 响应体
 * @throws ApiError 请求失败时抛出
 */
export async function apiPost<T>(
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  return response.json() as Promise<T>
}

/**
 * 发送 POST 请求，查询参数通过 URL 传递（如 abort?reason=xxx）。
 *
 * 与 apiPost 的区别：请求体为空，参数拼接到 URL 查询字符串中。
 * 用于后端通过 Query 参数接收数据的端点。
 *
 * @param path API 路径
 * @param params URL 查询参数
 * @returns 解析后的 JSON 响应体
 * @throws ApiError 请求失败时抛出
 */
export async function apiPostQuery<T>(
  path: string,
  params?: Record<string, string | undefined>,
): Promise<T> {
  let url = `${API_BASE}${path}`
  if (params) {
    const searchParams = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) {
        searchParams.set(key, value)
      }
    }
    const qs = searchParams.toString()
    if (qs) {
      url += `?${qs}`
    }
  }

  const response = await fetchWithTimeout(url, { method: 'POST' })
  return response.json() as Promise<T>
}

/**
 * 发送 DELETE 请求并解析 JSON 响应体。
 *
 * @param path API 路径
 * @returns 解析后的 JSON 响应体
 * @throws ApiError 请求失败时抛出
 */
export async function apiDelete<T>(
  path: string,
): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, { method: 'DELETE' })
  return response.json() as Promise<T>
}
