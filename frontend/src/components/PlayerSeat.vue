<script setup lang="ts">
/**
 * 单个玩家座位组件 —— 显示头像、身份牌、存活状态、发言高亮。
 *
 * **Why**: 设计中要求根据观战视角决定身份牌的渲染方式：
 * - God Mode（默认）：所有牌翻开，直接渲染身份牌图片
 * - POV Mode（预留）：仅目标玩家和自己的牌翻开
 * 当前纯 AI 对局默认 God View，POV 模式接口已预留。
 *
 * 布局策略：
 * - 左侧座位：卡牌在左，名字/座位号在右（flex-direction: row）
 * - 右侧座位：名字/座位号在左，卡牌在右（flex-direction: row-reverse）
 * 这样能彻底解决垂直空间不足导致的遮挡问题，同时保持界面对称。
 */

import { computed } from 'vue'
import type { PlayerState } from '../types/game'

const props = withDefaults(defineProps<{
  player: PlayerState
  /** 是否为当前发言者（高亮渲染） */
  isSpeaker?: boolean
  /** 座位所在列：左侧名字在右，右侧名字在左 */
  position?: 'left' | 'right'
}>(), {
  position: 'left'
})

/** 身份牌图片路径 */
const roleImage = computed(() => props.player.role_image || '')

/** 存活状态与发言高亮样式 */
const aliveClass = computed(() => ({
  'player-seat--dead': !props.player.is_alive,
  'player-seat--speaking': props.isSpeaker,
  [`player-seat--${props.position}`]: true
}))
</script>

<template>
  <div
    class="player-seat"
    :class="aliveClass"
  >
    <!-- 卡牌容器（用于定位光环和死亡标记） -->
    <div class="card-container">
      <div class="role-card">
        <img
          v-if="roleImage"
          :src="roleImage"
          :alt="player.role"
          class="role-image"
        />
        <div v-else class="role-placeholder">
          {{ player.role }}
        </div>
      </div>

      <!-- 死亡标记 -->
      <div v-if="!player.is_alive" class="death-mark">✕</div>

      <!-- 发言高亮光环 -->
      <div v-if="isSpeaker" class="speaking-ring" />
    </div>

    <!-- 玩家信息 -->
    <div class="player-info">
      <span class="player-id">{{ player.name }}</span>
      <span class="seat-number">座位 {{ player.seat_number }}</span>
    </div>
  </div>
</template>

<style scoped>
.player-seat {
  display: flex;
  align-items: center; /* 垂直居中卡牌和文字 */
  gap: 12px; /* 卡牌与文字之间的间距 */
  width: 100%;
  height: 100%;
  transition: transform 0.3s ease, opacity 0.3s ease;
}

/* 左侧座位：卡牌在左，文字在右 */
.player-seat--left {
  flex-direction: row;
  justify-content: flex-start;
}

/* 右侧座位：文字在左，卡牌在右 */
.player-seat--right {
  flex-direction: row-reverse;
  justify-content: flex-start;
}

.player-seat--dead {
  opacity: 0.5;
  filter: grayscale(100%);
}

.player-seat--speaking {
  transform: scale(1.15);
}

/* 卡牌外层容器，用于保持比例并定位 overlay 元素 */
.card-container {
  position: relative;
  height: 100%;
  max-height: 133px;
  aspect-ratio: 96 / 133;
  flex-shrink: 0; /* 防止卡牌被文字挤压变形 */
}

.role-card {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid rgba(255, 255, 255, 0.3);
  background: rgba(0, 0, 0, 0.5);
}

.role-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.role-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: #aaa;
  text-align: center;
}

/* 玩家信息 */
.player-info {
  display: flex;
  flex-direction: column;
  justify-content: center;
  white-space: nowrap; /* 防止名字换行 */
}

/* 左侧文字左对齐 */
.player-seat--left .player-info {
  align-items: flex-start;
}

/* 右侧文字右对齐 */
.player-seat--right .player-info {
  align-items: flex-end;
}

.player-id {
  font-weight: 600;
  color: #e0e0e0;
  font-size: 14px;
}

.seat-number {
  color: #888;
  font-size: 11px;
  margin-top: 4px;
}

/* 死亡标记 */
.death-mark {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 24px;
  height: 24px;
  background: #c62828;
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: bold;
  box-shadow: 0 0 8px rgba(198, 40, 40, 0.6);
  z-index: 10;
}

/* 发言高亮光环 */
.speaking-ring {
  position: absolute;
  inset: -6px;
  border-radius: 12px;
  border: 2px solid #ffd700;
  animation: pulse-ring 1.5s ease-in-out infinite;
  pointer-events: none;
}

@keyframes pulse-ring {
  0%, 100% {
    box-shadow: 0 0 8px rgba(255, 215, 0, 0.4);
  }
  50% {
    box-shadow: 0 0 20px rgba(255, 215, 0, 0.8);
  }
}
</style>
