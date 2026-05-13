/**
 * Vue 3 应用入口 —— 挂载 Pinia 状态管理和根组件。
 *
 * **Why**: Phase 3 前端通信层需要 Pinia 进行全局对局状态管理。
 * 不使用 vue-router（单页应用，通过组件 v-if 切换视图）。
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
