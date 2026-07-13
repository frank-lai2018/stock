<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { init, dispose } from 'klinecharts'
import { ElMessage } from 'element-plus'
import { getPrices } from '../api'

const props = defineProps({ stockId: { type: String, required: true } })

const el = ref(null)
let chart = null
const period = ref('D')     // D / W / M
const adj = ref(true)       // 還原
const BARS = { D: 250, W: 300, M: 180 }

async function load() {
  if (!chart) return
  try {
    const rows = await getPrices(props.stockId, {
      tf: period.value, bars: BARS[period.value], adj: adj.value ? 1 : 0,
    })
    const data = rows.map((r) => ({
      timestamp: new Date(r.trade_date).getTime(),
      open: +r.open, high: +r.high, low: +r.low, close: +r.close, volume: +r.volume,
    }))
    chart.applyNewData(data)
  } catch (e) {
    ElMessage.error('載入 K 線失敗：' + (e?.response?.data?.detail || e.message))
  }
}

const UP = '#EA4C4C'    // 漲：紅（台股慣例）
const DOWN = '#3F9E5A'  // 跌：綠

function onResize() {
  if (chart) chart.resize()
}

onMounted(() => {
  chart = init(el.value)
  // 台股紅漲綠跌
  chart.setStyles({
    candle: {
      bar: {
        upColor: UP, downColor: DOWN, noChangeColor: '#888888',
        upBorderColor: UP, downBorderColor: DOWN,
        upWickColor: UP, downWickColor: DOWN,
      },
    },
  })
  // 主圖疊 6 條均線
  chart.createIndicator({ name: 'MA', calcParams: [5, 10, 20, 60, 120, 240] }, true, { id: 'candle_pane' })
  // 副圖：量(含均量) / MACD / KDJ
  chart.createIndicator({ name: 'VOL', calcParams: [5, 20] })
  chart.createIndicator('MACD')
  chart.createIndicator('KDJ')
  load()
  window.addEventListener('resize', onResize)
})

watch([period, adj], load)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (el.value) dispose(el.value)
  chart = null
})
</script>

<template>
  <div>
    <div style="margin-bottom: 8px; display: flex; gap: 12px; align-items: center">
      <el-radio-group v-model="period" size="small">
        <el-radio-button value="D">日</el-radio-button>
        <el-radio-button value="W">週</el-radio-button>
        <el-radio-button value="M">月</el-radio-button>
      </el-radio-group>
      <el-switch v-model="adj" active-text="還原價" inline-prompt />
      <span style="color: #999; font-size: 12px">
        MA5/10/20/60/120/240｜量+均量｜MACD｜KD（皆由日線計算）
      </span>
    </div>
    <div ref="el" style="width: 100%; height: 1120px"></div>
  </div>
</template>
