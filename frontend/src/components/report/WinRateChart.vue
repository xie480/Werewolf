<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { TooltipComponent, GridComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'

use([LineChart, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  curveData?: Record<number, number>
}>()

const chartOption = computed(() => {
  if (!props.curveData || Object.keys(props.curveData).length === 0) {
    return null
  }

  const rounds = Object.keys(props.curveData).map(Number).sort((a, b) => a - b)
  const villagerProbs = rounds.map(r => props.curveData![r])
  const werewolfProbs = villagerProbs.map(p => 100 - p) // 假设数据是好人胜率 0-100

  return {
    tooltip: {
      trigger: 'axis',
      formatter: function (params: any) {
        let res = `第 ${params[0].axisValue} 轮<br/>`
        params.forEach((item: any) => {
          res += `${item.marker} ${item.seriesName}: ${item.value.toFixed(1)}%<br/>`
        })
        return res
      }
    },
    legend: {
      data: ['好人阵营胜率', '狼人阵营胜率'],
      textStyle: {
        color: '#a3a3a3'
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: rounds,
      axisLabel: {
        color: '#a3a3a3',
        formatter: '第 {value} 轮'
      },
      axisLine: {
        lineStyle: {
          color: '#4b5563'
        }
      }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: {
        color: '#a3a3a3',
        formatter: '{value}%'
      },
      splitLine: {
        lineStyle: {
          color: 'rgba(255, 255, 255, 0.1)'
        }
      }
    },
    series: [
      {
        name: '好人阵营胜率',
        type: 'line',
        data: villagerProbs,
        itemStyle: {
          color: '#3b82f6'
        },
        lineStyle: {
          width: 3
        },
        smooth: true
      },
      {
        name: '狼人阵营胜率',
        type: 'line',
        data: werewolfProbs,
        itemStyle: {
          color: '#ef4444'
        },
        lineStyle: {
          width: 3
        },
        smooth: true
      }
    ]
  }
})
</script>

<template>
  <div v-if="chartOption" class="win-rate-chart bg-gray-800/50 border border-gray-700 rounded-xl p-6 mb-8">
    <h3 class="text-xl font-bold text-white mb-4">阵营胜率走势</h3>
    <div class="w-full h-64">
      <v-chart class="w-full h-full" :option="chartOption" autoresize />
    </div>
  </div>
</template>
