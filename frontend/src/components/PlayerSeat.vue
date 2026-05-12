<script setup lang="ts">
/**
 * 单个玩家座位组件 —— 显示头像、身份牌、存活状态、发言高亮。
 *
 * **Why**: 设计中要求根据观战视角决定身份牌的渲染方式：
 * - God Mode（默认）：所有牌翻开，直接渲染身份牌图片
 * - POV Mode（预留）：仅目标玩家和自己的牌翻开
 * 当前纯 AI 对局默认 God View，POV 模式接口已预留。
 */

import { computed } from 'vue'
import type { PlayerState } from '../types/game'

const props = defineProps<{
  player: PlayerState
  /** 是否为当前发言者（高亮渲染） */
  isSpeaker?: boolean
}>()

/** 身份牌图片路径 */
const roleImage = computed(() => props.player.role_image || '')

/** 玩家的座位角度（用于环形布局定位） */
const seatAngle = computed(() => {
  // 9 人局：每个座位间隔 40° (360/9)
  // seat_number 从 1 开始
  const totalSeats = 9
  const anglePerSeat = 360 / totalSeats
  // 从顶部开始（-90°），顺时针排列
  return (props.player.seat_number - 1) * anglePerSeat - 90
})

/** 座位位置样式（由父组件或通过 CSS 变量传入环形参数） */
const seatStyle = computed(() => ({
  '--seat-angle': `${seatAngle.value}deg`,
}))

/** 存活状态样式 */
const aliveClass = computed(() => ({
  'player-seat--dead': !props.player.is_alive,
  'player-seat--speaking': props.isSpeaker,
}))
</script>

<template>
  <div
    class="player-seat"
    :class="aliveClass"
    :style="seatStyle"
  >
    <!-- 身份牌图片 -->
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

    <!-- 玩家信息 -->
    <div class="player-info">
      <span class="player-id">{{ player.player_id }}</span>
      <span class="seat-number">座位 {{ player.seat_number }}</span>
    </div>

    <!-- 死亡标记 -->
    <div v-if="!player.is_alive" class="death-mark">✕</div>

    <!-- 发言高亮光环 -->
    <div v-if="isSpeaker" class="speaking-ring" />
  </div>
</template>

<style scoped>
.player-seat {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 100px;
  transform: translate(-50%, -50%);
  transition: transform 0.3s ease, opacity 0.3s ease;
}

.player-seat--dead {
  opacity: 0.5;
  filter: grayscale(100%);
}

.player-seat--speaking {
  transform: translate(-50%, -50%) scale(1.15);
}

.role-card {
  width: 72px;
  height: 100px;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid rgba(255, 255, 255, 0.3);
  background: rgba(0, 0, 0, 0.5);
  position: relative;
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

.player-info {
  display: flex;
  flex-direction: column;
  align-items: center;
  font-size: 11px;
  color: #ccc;
}

.player-id {
  font-weight: 600;
  color: #e0e0e0;
}

.seat-number {
  color: #888;
  font-size: 10px;
}

.death-mark {
  position: absolute;
  top: -4px;
  right: -4px;
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
}

.speaking-ring {
  position: absolute;
  inset: -8px;
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
