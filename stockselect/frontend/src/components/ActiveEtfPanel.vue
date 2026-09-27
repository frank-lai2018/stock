<script setup>
// 個股頁「主動式 ETF 持有」：目前被哪些主動 ETF 持有、合計占股本、各 ETF 持股張數走勢、最近一次主動進出
// 資料：/api/active-etf/stock/:id（父層先抓，有持有才顯示這張卡）；說明見 主動ETF追蹤設計.md
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'

const props = defineProps({ data: { type: Object, required: true } })
const router = useRouter()
const el = ref(null)
let chart = null

const ACT = {
  new: { label: '新建倉', type: 'danger', effect: 'dark' },
  add: { label: '加碼', type: 'danger', effect: 'plain' },
  add_rel: { label: '相對加碼', type: 'warning', effect: 'plain' },
  exit: { label: '出清', type: 'success', effect: 'dark' },
  cut: { label: '減碼', type: 'success', effect: 'plain' },
  cut_rel: { label: '相對減碼', type: 'info', effect: 'plain' },
}
const COLORS = ['#EA4C4C', '#409eff', '#e6a23c', '#3F9E5A', '#9b59b6', '#16a085', '#7f8c8d']
const up = (v) => (v == null ? '#999' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
const lots = (v) => (v == null ? '—' : (Number(v) > 0 ? '+' : '') + Math.round(Number(v) / 1000).toLocaleString('en-US') + ' 張')
const lotsAbs = (v) => (v == null ? '—' : Math.round(Number(v) / 1000).toLocaleString('en-US'))
const yi = (v) => (v == null ? '—' : (Number(v) > 0 ? '+' : '') + (Number(v) / 1e8).toFixed(1) + ' 億')

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const series = props.data.series || []
  chart.setOption({
    grid: { left: 8, right: 8, top: 30, bottom: 24, containLabel: true },
    legend: { top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : Number(v).toLocaleString('en-US') + ' 張') },
    xAxis: { type: 'category', data: (series[0]?.points || []).map((p) => p[0]), axisLabel: { formatter: (v) => v.slice(5) } },
    yAxis: { type: 'value', name: '張', splitLine: { lineStyle: { color: '#f0f0f0' } } },
    series: series.map((s, i) => ({
      name: s.etf_id, type: 'line', step: 'end', showSymbol: false, itemStyle: { color: COLORS[i % COLORS.length] },
      data: s.points.map((p) => Math.round(p[1] / 1000)),
    })),
  }, true)
  chart.resize()
}

function onResize() { if (chart) chart.resize() }
onMounted(async () => { await nextTick(); render(); window.addEventListener('resize', onResize) })
watch(() => props.data, () => nextTick(render))
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); if (chart) chart.dispose() })
</script>

<template>
  <div>
    <div style="margin-bottom: 8px">
      目前被 <b>{{ data.holders.filter((h) => h.weight != null && Number(h.weight) >= 0.01).length }}</b> 檔主動 ETF 持有，
      合計 <b>{{ lotsAbs(data.total_shares) }}</b> 張
      <template v-if="data.held_pct != null">，占股本 <b>{{ (data.held_pct * 100).toFixed(2) }}%</b></template>
    </div>
    <el-table :data="data.holders" size="small" stripe style="cursor: pointer"
              @row-click="(r) => router.push({ path: '/active-etf', query: { etf: r.etf_id } })">
      <el-table-column label="ETF" min-width="170" show-overflow-tooltip>
        <template #default="{ row }">{{ row.etf_id }} {{ row.name }}</template>
      </el-table-column>
      <el-table-column prop="issuer" label="投信" width="64" />
      <el-table-column label="張數" width="90" align="right">
        <template #default="{ row }">{{ row.weight != null && Number(row.weight) >= 0.01 ? lotsAbs(row.shares) : '—' }}</template>
      </el-table-column>
      <el-table-column label="權重" width="76" align="right">
        <template #default="{ row }">{{ row.weight != null && Number(row.weight) >= 0.01 ? Number(row.weight).toFixed(2) + '%' : '未持有' }}</template>
      </el-table-column>
      <el-table-column prop="as_of" label="持股日" width="96" />
      <el-table-column label="最近一次主動進出" min-width="230">
        <template #default="{ row }">
          <template v-if="row.last_move">
            <el-tag :type="ACT[row.last_move.action]?.type" :effect="ACT[row.last_move.action]?.effect" size="small">
              {{ ACT[row.last_move.action]?.label || row.last_move.action }}
            </el-tag>
            <span style="margin-left: 6px">{{ row.last_move.date }}</span>
            <span style="margin-left: 6px" :style="{ color: up(row.last_move.active_shares) }">
              {{ lots(row.last_move.active_shares) }}（{{ yi(row.last_move.active_amount) }}）
            </span>
          </template>
          <span v-else style="color: #ccc">—</span>
        </template>
      </el-table-column>
    </el-table>
    <div ref="el" style="width: 100%; height: 220px; margin-top: 8px"></div>
    <div style="color: #999; font-size: 12px">
      主動進出已扣掉「全面等比例增減」（申購贖回或全面調整持股水位），只留經理人針對這檔的調整。
      只含目前支援的投信；持股資料收盤後才公布。點列看該 ETF 的完整異動。
    </div>
  </div>
</template>
