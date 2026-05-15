<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'
import type { AgentEvaluationResponse } from '../../types/api'

use([RadarChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  evaluation: AgentEvaluationResponse
}>()

const isWolf = computed(() => props.evaluation.role === 'WEREWOLF')

const chartOption = computed(() => {
  const ev = props.evaluation
  
  // 基础维度
  const indicator = [
    { name: '规则服从', max: 100 },
    { name: '逻辑连贯', max: 100 },
    { name: '角色扮演', max: 10 },
  ]
  
  const values = [
    ev.rule_compliance_score,
    ev.logical_consistency_score,
    ev.roleplay_score,
  ]

  // 专属维度
  if (isWolf.value) {
    indicator.push(
      { name: '伪装欺骗', max: 10 },
      { name: '找神能力', max: 10 }
    )
    values.push(
      ev.deception_score ?? 0,
      ev.god_deduction_score ?? 0
    )
  } else {
    indicator.push(
      { name: '态势感知', max: 100 },
      { name: '统帅引导', max: 10 }
    )
    values.push(
      ev.situational_awareness_score ?? 0,
      ev.leadership_score ?? 0
    )
  }

  return {
    tooltip: {
      trigger: 'item'
    },
    radar: {
      indicator,
      radius: '65%',
      splitNumber: 4,
      axisName: {
        color: '#a3a3a3',
        fontSize: 12
      },
      splitLine: {
        lineStyle: {
          color: ['rgba(255, 255, 255, 0.1)', 'rgba(255, 255, 255, 0.2)', 'rgba(255, 255, 255, 0.4)', 'rgba(255, 255, 255, 0.6)', 'rgba(255, 255, 255, 0.8)'].reverse()
        }
      },
      splitArea: {
        show: false
      },
      axisLine: {
        lineStyle: {
          color: 'rgba(255, 255, 255, 0.2)'
        }
      }
    },
    series: [
      {
        name: '五维评分',
        type: 'radar',
        data: [
          {
            value: values,
            name: props.evaluation.player_id,
            itemStyle: {
              color: isWolf.value ? '#ef4444' : '#3b82f6'
            },
            areaStyle: {
              color: isWolf.value ? 'rgba(239, 68, 68, 0.3)' : 'rgba(59, 130, 246, 0.3)'
            }
          }
        ]
      }
    ]
  }
})
</script>

<template>
  <div class="radar-chart-container w-full h-64">
    <v-chart class="w-full h-full" :option="chartOption" autoresize />
  </div>
</template>
