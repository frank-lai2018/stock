<script setup>
// 個股頁「主動式 ETF 持有」：被幾家投信持有（只算規模 ≥1%）、是否為持股籃主策略成分股、合計占股本、
// 各 ETF 持股張數走勢、近 20 個持股日的每單位持股變化、最近一次主動進出、每日對照投信買賣超
// 資料：/api/active-etf/stock/:id（父層先抓，有持有才顯示這張卡）；說明見 主動ETF追蹤設計.md
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'

const props = defineProps({ data: { type: Object, required: true } })
const router = useRouter()
const el = ref(null)
const vtEl = ref(null)
let chart = null
let vtChart = null

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
const spct = (v) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + (Number(v) * 100).toFixed(1) + '%')

// 近 20 個持股日：主動 ETF 合計實際買賣 vs 投信買賣超（主動 ETF 是投信的一部分，方向通常一致）
const vtRows = computed(() => (props.data.vs_trust || []).filter((r) => Number(r.etf_shares) || r.trust_net))

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

function renderVs() {
  if (!vtEl.value || !vtRows.value.length) return
  if (vtChart && vtChart.getDom() !== vtEl.value) { vtChart.dispose(); vtChart = null }   // 換股票時 v-if 重建了容器
  if (!vtChart) vtChart = echarts.init(vtEl.value)
  const rs = props.data.vs_trust || []
  vtChart.setOption({
    grid: { left: 8, right: 8, top: 30, bottom: 24, containLabel: true },
    legend: { top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : Number(v).toLocaleString('en-US') + ' 張') },
    xAxis: { type: 'category', data: rs.map((r) => r.trade_date), axisLabel: { formatter: (v) => v.slice(5) } },
    yAxis: { type: 'value', name: '張', splitLine: { lineStyle: { color: '#f0f0f0' } } },
    series: [
      { name: '主動 ETF 合計', type: 'bar', barGap: 0, barMaxWidth: 10, itemStyle: { color: '#EA4C4C' },
        data: rs.map((r) => Math.round(Number(r.etf_shares || 0) / 1000)) },
      { name: '投信買賣超', type: 'bar', barMaxWidth: 10, itemStyle: { color: '#909399' },
        data: rs.map((r) => (r.trust_net == null ? null : Math.round(Number(r.trust_net) / 1000))) },
    ],
  }, true)
  vtChart.resize()
}

function onResize() { if (chart) chart.resize(); if (vtChart) vtChart.resize() }
onMounted(async () => { await nextTick(); render(); renderVs(); window.addEventListener('resize', onResize) })
watch(() => props.data, () => nextTick(() => { render(); renderVs() }))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) chart.dispose()
  if (vtChart) vtChart.dispose()
})
</script>

<template>
  <div>
    <div style="margin-bottom: 8px; display: flex; align-items: center; flex-wrap: wrap; gap: 8px">
      <span>
        目前被 <b>{{ data.n_issuers }}</b> 家投信（<b>{{ data.n_etfs }}</b> 檔主動 ETF）持有，
        合計 <b>{{ lotsAbs(data.total_shares) }}</b> 張
        <template v-if="data.held_pct != null">，占股本 <b>{{ (data.held_pct * 100).toFixed(2) }}%</b></template>
      </span>
      <el-tag v-if="data.in_basket" type="danger" effect="dark" size="small">
        持股籃主策略成分股（≥{{ data.basket_min }} 家）
      </el-tag>
      <span v-else style="color: #999; font-size: 12px">（≥{{ data.basket_min }} 家才是持股籃主策略成分股）</span>
      <span v-if="data.n_issuers_minor" style="color: #999; font-size: 12px">
        另有 {{ data.n_issuers_minor }} 家小投信持有（規模 &lt;1%，不計入家數）
      </span>
    </div>
    <el-table :data="data.holders" size="small" stripe style="cursor: pointer"
              @row-click="(r) => router.push({ path: '/active-etf', query: { etf: r.etf_id } })">
      <el-table-column label="ETF" min-width="170" show-overflow-tooltip>
        <template #default="{ row }">{{ row.etf_id }} {{ row.name }}</template>
      </el-table-column>
      <el-table-column label="投信" width="72">
        <template #default="{ row }">
          <span v-if="row.major">{{ row.issuer }}</span>
          <el-tooltip v-else content="規模不到 1% 的投信：照列，但不計入家數"><span style="color: #bbb">{{ row.issuer }}</span></el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="張數" width="90" align="right">
        <template #default="{ row }">{{ row.real ? lotsAbs(row.shares) : '—' }}</template>
      </el-table-column>
      <el-table-column label="權重" width="76" align="right">
        <template #default="{ row }">{{ row.real ? Number(row.weight).toFixed(2) + '%' : '未持有' }}</template>
      </el-table-column>
      <el-table-column :label="`${data.pu_window} 日每單位`" width="100" align="right">
        <template #default="{ row }">
          <span v-if="row.pu_status === 'new'" style="color: #EA4C4C">新建倉</span>
          <span v-else-if="row.pu_status === 'exit'" style="color: #3F9E5A">出清</span>
          <span v-else-if="row.pu != null" :style="{ color: Math.abs(row.pu) >= 0.1 ? up(row.pu) : '#999' }">{{ spct(row.pu) }}</span>
          <span v-else style="color: #ccc">—</span>
        </template>
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
    <template v-if="vtRows.length">
      <div style="font-weight: 600; margin-top: 8px">主動 ETF vs 投信買賣超（近 {{ (data.vs_trust || []).length }} 個持股日）</div>
      <div ref="vtEl" style="width: 100%; height: 200px"></div>
    </template>
    <div style="color: #999; font-size: 12px">
      主動進出已扣掉「全面等比例增減」（申購贖回或全面調整持股水位），只留經理人針對這檔的調整。
      {{ data.pu_window }} 日每單位＝每單位持股（股數 ÷ 單位數）跟 {{ data.pu_window }} 個持股日前比的變化，扣掉申購贖回；±10% 以上才上色。
      家數只算規模 ≥1% 的投信，≥{{ data.basket_min }} 家＝持股籃主策略（回測贏 00981A，見「主動ETF」頁）。
      投信買賣超包含主動 ETF 以外的投信基金，兩者方向通常一致。持股資料收盤後才公布；點列看該 ETF 的完整異動。
    </div>
  </div>
</template>
