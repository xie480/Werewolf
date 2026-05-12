/**
 * 类型定义 barrel 导出 —— 统一入口。
 *
 * **Why**: 其他模块只需 `import { GamePhase, PlayerState } from '@/types'`，
 * 无需记忆具体文件路径。
 */

export * from './enums'
export * from './api'
export * from './websocket'
export * from './game'
