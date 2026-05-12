<script setup lang="ts">
/**
 * 法官播报横幅 —— SYSTEM_ANNOUNCEMENT 事件触发时下拉显示。
 *
 * **Why**: 设计中要求在屏幕中央顶部设计横向跑马灯/卷轴横幅，
 * 收到系统公告事件时下拉展开显示文字（如"昨夜，3号玩家死亡"），
 * 停留 3 秒后自动收起。
 */

import { computed } from 'vue'
import { useGameStore } from '../store/game'

const store = useGameStore()

/** 是否有活跃的公告 */
const isVisible = computed(() => !!store.announcement)
</script>

<template>
  <Transition name="banner">
    <div v-if="isVisible" class="announcement-banner">
      <div class="banner-content">
        <span class="banner-icon">📜</span>
        <span class="banner-text">{{ store.announcement }}</span>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.announcement-banner {
  position: fixed;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  z-index: 200;
  width: 80%;
  max-width: 640px;
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.95), rgba(0, 0, 0, 0.8));
  border-bottom: 2px solid rgba(255, 215, 0, 0.4);
  border-radius: 0 0 16px 16px;
  padding: 16px 24px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6);
}

.banner-content {
  display: flex;
  align-items: center;
  gap: 12px;
}

.banner-icon {
  font-size: 22px;
  flex-shrink: 0;
}

.banner-text {
  font-size: 16px;
  color: #ffd700;
  line-height: 1.5;
  text-align: center;
  flex: 1;
}

/* 下拉/收起动画 */
.banner-enter-active {
  transition: all 0.5s ease-out;
}
.banner-leave-active {
  transition: all 0.4s ease-in;
}
.banner-enter-from {
  transform: translateX(-50%) translateY(-100%);
  opacity: 0;
}
.banner-leave-to {
  transform: translateX(-50%) translateY(-100%);
  opacity: 0;
}
</style>
