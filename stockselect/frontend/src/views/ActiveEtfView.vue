<script setup>
// 主動ETF動向：各投信官網每日公告的持股，相鄰兩天相減、扣掉全面等比例的增減（申購贖回、全面調整水位）
// 上：跨投信共識買進／賣出＋訊號回測（跟著買有沒有用）　中：各 ETF 概況　下：單檔明細（曝險走勢、當日異動、持股）
// 資料由 fetch_active_etf.py 每晚抓（nightly 的 etfhold 工作）、回測由 backtest_etf_flow.py 每週跑；說明見 主動ETF追蹤設計.md
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getActiveEtfOverview, getActiveEtfConsensus, getActiveEtfFund, getActiveEtfBacktest,
  getActiveEtfBacktestEvents } from '../api'

const route = useRoute()
const router = useRouter()
const ov = ref(null)
const date = ref(null)
const days = ref(1)
const buys = ref([])
const sells = ref([])
const fund = ref(null)
const fundDate = ref(null)
const tab = ref('flows')
const loading = ref(false)
const cLoading = ref(false)
const fLoading = ref(false)
const chartEl = ref(null)
let chart = null

const ACT = {
  new: { label: '新建倉', type: 'danger', effect: 'dark' },
  add: { label: '加碼', type: 'danger', effect: 'plain' },
  add_rel: { label: '相對加碼', type: 'warning', effect: 'plain' },
  exit: { label: '出清', type: 'success', effect: 'dark' },
  cut: { label: '減碼', type: 'success', effect: 'plain' },
  cut_rel: { label: '相對減碼', type: 'info', effect: 'plain' },
  flow: { label: '隨等比例', type: 'info', effect: 'plain' },
  corp: { label: '除權/減資', type: 'info', effect: 'plain' },
}
const BUY = ['new', 'add']
const SELL = ['exit', 'cut']

const up = (v) => (v == null ? '#999' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
const yi = (v, sign = true) => (v == null ? '—' : (sign && Number(v) > 0 ? '+' : '') + (Number(v) / 1e8).toFixed(1) + ' 億')
const lots = (v) => (v == null ? '—' : (Number(v) > 0 ? '+' : '') + Math.round(Number(v) / 1000).toLocaleString('en-US') + ' 張')
const lotsAbs = (v) => (v == null ? '—' : Math.round(Number(v) / 1000).toLocaleString('en-US'))
const pct = (v, d = 1) => (v == null ? '—' : (Number(v) * 100).toFixed(d) + '%')
const kc = (k) => up(k == null ? null : Number(k) - 1)          // 比例 k 的顏色（null 不上色）
const kpct = (k) => (k == null ? '—' : ((Number(k) - 1) >= 0 ? '+' : '') + ((Number(k) - 1) * 100).toFixed(2) + '%')
const w = (v) => (v == null ? '—' : Number(v).toFixed(2) + '%')

const coverageText = computed(() => {
  const c = ov.value?.coverage
  if (!c) return ''
  return `涵蓋 ${c.n_covered}/${c.n_total} 檔（${c.issuers.join('、')}），約占台股主動 ETF 規模 ${pct(c.pct, 0)}`
})

function sideEtfs(row, acts) {
  const seen = new Map()
  for (const x of row.detail || []) if (acts.includes(x.action) && !seen.has(x.etf_id)) seen.set(x.etf_id, x)
  return [...seen.values()]
}

// ---- 訊號回測（backtest_etf_flow.py；判讀規則在後端 routers/active_etf.py 的 _verdict）----
const bt = ref(null)
const btH = ref(20)
const btSig = ref(null)
const btEvents = ref([])
const btLoading = ref(false)
const spct = (v, d = 2) => (v == null ? '—' : (Number(v) >= 0 ? '+' : '') + (Number(v) * 100).toFixed(d) + '%')
const btRows = computed(() => (bt.value?.rows || []).filter((r) => r.horizon === btH.value))
const btRange = computed(() => {
  const rs = btRows.value
  if (!rs.length) return ''
  const lo = rs.map((r) => r.date_from).filter(Boolean).sort()[0]
  const hi = rs.map((r) => r.date_to).filter(Boolean).sort().slice(-1)[0]
  return `訊號日 ${lo} ~ ${hi}`
})
function verdict(r) {
  if (r.verdict === 'effective') return r.sign < 0 ? { label: '有效（避開）', type: 'success' } : { label: '有效', type: 'danger' }
  if (r.verdict === 'reverse') return { label: '反向', type: 'warning' }
  return { label: '不顯著', type: 'info' }
}
async function loadBacktest() {
  try { bt.value = await getActiveEtfBacktest() } catch (e) { /* 還沒跑過回測 → 不顯示 */ }
}
async function openSignal(sig) {
  btSig.value = sig
  if (sig === 'basket') { btEvents.value = []; return }
  btLoading.value = true
  try { btEvents.value = await getActiveEtfBacktestEvents(sig, btH.value, 100) } finally { btLoading.value = false }
}
function onBtH() { if (btSig.value) openSignal(btSig.value) }
function btRowClass({ row }) { return row.signal === btSig.value ? 'current-row-etf' : '' }

async function load() {
  loading.value = true
  loadBacktest()                                  // 獨立載入：回測表不存在也不影響其他區塊
  try {
    ov.value = await getActiveEtfOverview()
    date.value = ov.value.as_of
    await loadConsensus()
    const want = String(route.query.etf || '').toUpperCase()
    const first = ov.value.funds.find((f) => f.etf_id === want) || ov.value.funds[0]
    if (want && first?.etf_id !== want) ElMessage.info(`${want} 還沒支援抓取（目前涵蓋 ${ov.value.coverage.issuers.join('、')}）`)
    if (first) await openFund(first.etf_id, date.value)
  } catch (e) {
    ElMessage.error('載入主動 ETF 資料失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadConsensus() {
  if (!date.value) return
  cLoading.value = true
  try {
    const [b, s] = await Promise.all([
      getActiveEtfConsensus({ date: date.value, days: days.value, side: 'buy', limit: 40 }),
      getActiveEtfConsensus({ date: date.value, days: days.value, side: 'sell', limit: 40 }),
    ])
    buys.value = b.rows
    sells.value = s.rows
  } finally {
    cLoading.value = false
  }
}

async function openFund(etfId, d) {
  fLoading.value = true
  try {
    fund.value = await getActiveEtfFund(etfId, d || undefined)
    fundDate.value = fund.value.as_of
    await nextTick()
    renderChart()
  } catch (e) {
    ElMessage.error('載入 ETF 明細失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    fLoading.value = false
  }
}

function renderChart() {
  if (!chartEl.value || !fund.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const h = fund.value.history || []
  const x = h.map((s) => s.as_of)
  const sw = h.map((s) => (s.stock_weight == null ? null : Number(s.stock_weight)))
  const fw = h.map((s) => (s.futures_weight == null ? null : Number(s.futures_weight)))
  chart.setOption({
    grid: { left: 8, right: 8, top: 36, bottom: 28, containLabel: true },
    legend: { top: 0 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: x, axisLabel: { formatter: (v) => v.slice(2) } },
    yAxis: [
      { type: 'value', name: '%', scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } } },
      { type: 'value', name: '億單位', scale: true, splitLine: { show: false } },
    ],
    series: [
      { name: '股票＋期貨', type: 'line', showSymbol: false, data: sw.map((v, i) => (v == null ? null : +(v + (fw[i] || 0)).toFixed(2))),
        lineStyle: { width: 2 }, itemStyle: { color: '#EA4C4C' } },
      { name: '股票', type: 'line', showSymbol: false, data: sw, itemStyle: { color: '#e6a23c' } },
      { name: '期貨', type: 'line', showSymbol: false, data: fw, itemStyle: { color: '#409eff' } },
      { name: '單位數', type: 'bar', yAxisIndex: 1, barMaxWidth: 6, itemStyle: { color: '#dcdfe6' },
        data: h.map((s) => (s.units == null ? null : +(Number(s.units) / 1e8).toFixed(2))) },
    ],
  }, true)
  chart.resize()
}

function onDate() {
  loadConsensus()
  if (fund.value && fund.value.dates.includes(date.value)) openFund(fund.value.etf_id, date.value)
}
function fundRowClass({ row }) { return fund.value && row.etf_id === fund.value.etf_id ? 'current-row-etf' : '' }
function onResize() { if (chart) chart.resize() }
function go(id) { router.push(`/stock/${id}`) }

onMounted(async () => {
  await load()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) chart.dispose()
})
</script>

<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 12px">
          <b>主動ETF動向</b>
          <el-select v-model="date" size="small" style="width: 140px" @change="onDate">
            <el-option v-for="d in ov?.dates || []" :key="d" :label="d" :value="d" />
          </el-select>
          <el-radio-group v-model="days" size="small" @change="loadConsensus">
            <el-radio-button :value="1">當日</el-radio-button>
            <el-radio-button :value="5">近 5 日</el-radio-button>
            <el-radio-button :value="20">近 20 日</el-radio-button>
          </el-radio-group>
          <span style="color: #999; font-size: 12px">{{ coverageText }}</span>
        </div>
      </template>
      <el-row :gutter="16" v-loading="cLoading">
        <el-col v-for="side in [{ key: 'buy', title: '共識買進', rows: buys, acts: BUY, n: 'n_buy' },
                                { key: 'sell', title: '共識賣出', rows: sells, acts: SELL, n: 'n_sell' }]"
                :key="side.key" :xs="24" :lg="12">
          <div style="font-weight: 600; margin-bottom: 6px" :style="{ color: side.key === 'buy' ? '#EA4C4C' : '#3F9E5A' }">
            {{ side.title }}<span style="color: #999; font-weight: 400; font-size: 12px">（依投信家數、再依主動金額排序；只列淨方向一致的）</span>
          </div>
          <el-table :data="side.rows" stripe height="400" size="small" style="cursor: pointer"
                    @row-click="(r) => go(r.stock_id)">
            <el-table-column label="股票" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
            </el-table-column>
            <el-table-column label="家數" width="56" align="center">
              <template #default="{ row }"><b>{{ row[side.n] }}</b></template>
            </el-table-column>
            <el-table-column label="主動金額" width="92" align="right">
              <template #default="{ row }"><span :style="{ color: up(row.active_amount) }">{{ yi(row.active_amount) }}</span></template>
            </el-table-column>
            <el-table-column label="實際買賣" width="92" align="right">
              <template #default="{ row }"><span :style="{ color: up(row.amount) }">{{ yi(row.amount) }}</span></template>
            </el-table-column>
            <el-table-column label="占成交額" width="76" align="right">
              <template #default="{ row }">{{ row.impact == null ? '—' : pct(Math.abs(row.impact)) }}</template>
            </el-table-column>
            <el-table-column label="持股占股本" width="86" align="right">
              <template #default="{ row }">{{ pct(row.held_pct, 2) }}</template>
            </el-table-column>
            <el-table-column label="ETF" min-width="150">
              <template #default="{ row }">
                <el-tooltip v-for="x in sideEtfs(row, side.acts)" :key="x.etf_id"
                            :content="`${x.date}　${ACT[x.action].label} ${lots(x.active_shares)}（${yi(x.active_amount)}）`">
                  <el-tag :type="ACT[x.action].type" :effect="ACT[x.action].effect" size="small" style="margin: 1px 4px 1px 0">
                    {{ x.etf_id }}
                  </el-tag>
                </el-tooltip>
              </template>
            </el-table-column>
          </el-table>
        </el-col>
      </el-row>
      <div style="color: #999; font-size: 12px; margin-top: 8px">
        主動金額＝扣掉「全面等比例增減」後的調整（申購贖回或全面調整持股水位不算）；實際買賣＝股數變化 × 收盤價，是市場上真實的買賣壓力。
        共識以投信家數計，同一家投信的兩檔 ETF 只算一家。占成交額＝實際買賣 ÷ 期間成交金額；持股占股本＝目前涵蓋的主動 ETF 合計持股 ÷ 發行股數。
        持股資料收盤後才公布，最早只能隔天開盤反應。跟著買有沒有用，看下方「訊號回測」。
      </div>
    </el-card>

    <el-card v-if="bt && bt.rows.length" shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 12px">
          <b>訊號回測</b>
          <el-radio-group v-model="btH" size="small" @change="onBtH">
            <el-radio-button :value="5">持有 5 日</el-radio-button>
            <el-radio-button :value="10">10 日</el-radio-button>
            <el-radio-button :value="20">20 日</el-radio-button>
          </el-radio-group>
          <span style="color: #999; font-size: 12px">
            跟著主動 ETF 進出買賣有沒有超額｜{{ btRange }}｜每週更新（{{ String(bt.computed_at || '').slice(0, 10) }}）｜點列看逐筆事件
          </span>
        </div>
      </template>
      <el-table :data="btRows" size="small" stripe style="cursor: pointer" :row-class-name="btRowClass"
                @row-click="(r) => openSignal(r.signal)">
        <el-table-column label="訊號" min-width="220">
          <template #default="{ row }">
            <b>{{ row.name }}</b><span style="color: #999; font-size: 12px; margin-left: 6px">{{ row.note }}</span>
          </template>
        </el-table-column>
        <el-table-column label="判讀" width="104">
          <template #default="{ row }">
            <el-tag :type="verdict(row).type" size="small" effect="plain">{{ verdict(row).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="n" label="樣本" width="62" align="right" />
        <el-table-column label="超額（持股籃）" width="112" align="right">
          <template #default="{ row }">
            <span v-if="row.signal !== 'basket'" :style="{ color: up(row.avg_excess_basket) }">{{ spct(row.avg_excess_basket) }}</span>
            <span v-else style="color: #ccc">—</span>
          </template>
        </el-table-column>
        <el-table-column label="超額（大盤）" width="100" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.avg_excess_mkt) }">{{ spct(row.avg_excess_mkt) }}</span></template>
        </el-table-column>
        <el-table-column label="中位數" width="80" align="right">
          <template #default="{ row }">{{ spct(row.median_excess_basket) }}</template>
        </el-table-column>
        <el-table-column label="命中率" width="68" align="right">
          <template #default="{ row }">{{ pct(row.hit, 0) }}</template>
        </el-table-column>
        <el-table-column label="t 值" width="60" align="right">
          <template #default="{ row }">{{ row.t_stat == null ? '—' : (Number(row.t_stat) > 0 ? '+' : '') + Number(row.t_stat).toFixed(1) }}</template>
        </el-table-column>
        <el-table-column label="月份同向" width="80" align="center">
          <template #default="{ row }">{{ row.months_ok }}/{{ row.months }}</template>
        </el-table-column>
        <el-table-column label="隔日開盤跳空" width="100" align="right">
          <template #default="{ row }">{{ spct(row.gap_ex) }}</template>
        </el-table-column>
      </el-table>
      <div style="color: #999; font-size: 12px; margin-top: 8px; line-height: 1.7">
        T+1 開盤進場（持股資料收盤後才公布）、還原價、未扣成本。超額（持股籃）＝減掉當天所有主動 ETF 持股的平均，
        排除「主動 ETF 本來就挑強勢股」的效果；「持股籃本身」那列是持股籃跟大盤比。
        判讀：往預期方向超過 {{ pct(bt.cost, 1) }}（來回成本）、|t| ≥ 2、六成以上月份同方向才標「有效」；明顯往反方向標「反向」。
        命中率、月份同向已依方向調整（賣出訊號算「之後輸給持股籃」的比例）。
        t 值把每個事件當獨立樣本，但事件在時間上會重疊，實際顯著性比表上低；歷史從 2025-05 開始，多半是科技股多頭，結果有時期依賴。
      </div>
      <template v-if="btSig && btSig !== 'basket'">
        <div style="font-weight: 600; margin: 12px 0 6px">
          {{ btRows.find((r) => r.signal === btSig)?.name }}：最近 100 筆事件（持有 {{ btH }} 日，還沒滿期的顯示 —）
        </div>
        <el-table :data="btEvents" v-loading="btLoading" size="small" stripe max-height="360" style="cursor: pointer"
                  @row-click="(r) => go(r.stock_id)">
          <el-table-column label="股票" min-width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
          </el-table-column>
          <el-table-column prop="industry" label="產業" width="100" show-overflow-tooltip />
          <el-table-column prop="trade_date" label="訊號日" width="100" />
          <el-table-column prop="n_issuers" label="家數" width="56" align="center" />
          <el-table-column label="申購期" width="64" align="center">
            <template #default="{ row }">{{ row.inflow ? '是' : '' }}</template>
          </el-table-column>
          <el-table-column label="報酬" width="84" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.ret) }">{{ spct(row.ret, 1) }}</span></template>
          </el-table-column>
          <el-table-column label="超額（持股籃）" width="112" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.excess_basket) }">{{ spct(row.excess_basket, 1) }}</span></template>
          </el-table-column>
          <el-table-column label="隔日開盤跳空" width="100" align="right">
            <template #default="{ row }">{{ spct(row.gap_ex, 1) }}</template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 12px">
          <b>各 ETF 概況</b>
          <span style="color: #999; font-size: 12px">各檔最新持股日｜點列看明細</span>
        </div>
      </template>
      <el-table :data="ov?.funds || []" stripe size="small" style="cursor: pointer" :row-class-name="fundRowClass"
                @row-click="(r) => openFund(r.etf_id, date)">
        <el-table-column label="ETF" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ row.etf_id }} {{ row.name }}</template>
        </el-table-column>
        <el-table-column prop="issuer" label="投信" width="64" />
        <el-table-column label="持股日" width="96">
          <template #default="{ row }">
            <span :style="{ color: row.as_of === ov.as_of ? '' : '#e6a23c' }">{{ row.as_of || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="規模" width="84" align="right">
          <template #default="{ row }">{{ yi(row.nav_total, false) }}</template>
        </el-table-column>
        <el-table-column label="單位數" width="78" align="right">
          <template #default="{ row }"><span :style="{ color: kc(row.flow_k_units) }">{{ kpct(row.flow_k_units) }}</span></template>
        </el-table-column>
        <el-table-column label="全面增減" width="80" align="right">
          <template #default="{ row }"><span :style="{ color: kc(row.flow_k) }">{{ kpct(row.flow_k) }}</span></template>
        </el-table-column>
        <el-table-column label="股票／期貨" width="116" align="right">
          <template #default="{ row }">{{ w(row.stock_weight) }}／{{ w(row.futures_weight) }}</template>
        </el-table-column>
        <el-table-column label="新建倉" width="62" align="center">
          <template #default="{ row }"><span style="color: #EA4C4C">{{ row.n_new || '' }}</span></template>
        </el-table-column>
        <el-table-column label="加碼" width="52" align="center">
          <template #default="{ row }"><span style="color: #EA4C4C">{{ row.n_add || '' }}</span></template>
        </el-table-column>
        <el-table-column label="減碼" width="52" align="center">
          <template #default="{ row }"><span style="color: #3F9E5A">{{ row.n_cut || '' }}</span></template>
        </el-table-column>
        <el-table-column label="出清" width="52" align="center">
          <template #default="{ row }"><span style="color: #3F9E5A">{{ row.n_exit || '' }}</span></template>
        </el-table-column>
        <el-table-column label="實際買／賣" width="140" align="right">
          <template #default="{ row }">
            <span style="color: #EA4C4C">{{ yi(row.buy_amt) }}</span>／<span style="color: #3F9E5A">{{ yi(row.sell_amt) }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="ov?.coverage?.uncovered?.length" style="color: #999; font-size: 12px; margin-top: 8px">
        尚未支援：{{ ov.coverage.uncovered.map((u) => `${u.etf_id}（${u.issuer}）`).join('、') }}
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px" v-loading="fLoading">
      <template #header>
        <div v-if="fund" style="display: flex; align-items: baseline; flex-wrap: wrap; gap: 14px">
          <b style="font-size: 16px">{{ fund.etf_id }} {{ fund.name }}</b>
          <span style="color: #666">{{ fund.issuer }}</span>
          <el-select v-model="fundDate" size="small" style="width: 140px" @change="(d) => openFund(fund.etf_id, d)">
            <el-option v-for="d in fund.dates" :key="d" :label="d" :value="d" />
          </el-select>
          <template v-if="fund.snapshot">
            <span>規模 <b>{{ yi(fund.snapshot.nav_total, false) }}</b></span>
            <span>比較 {{ fund.snapshot.prev_as_of || '—' }}：單位數 <b :style="{ color: kc(fund.snapshot.flow_k_units) }">{{ kpct(fund.snapshot.flow_k_units) }}</b>、
              全面增減 <b :style="{ color: kc(fund.snapshot.flow_k) }">{{ kpct(fund.snapshot.flow_k) }}</b></span>
            <span>股票 <b>{{ w(fund.snapshot.stock_weight) }}</b>＋期貨 <b>{{ w(fund.snapshot.futures_weight) }}</b></span>
            <span v-for="f in fund.futures" :key="f.code" style="color: #666">{{ f.name }} {{ Number(f.contracts).toLocaleString('en-US') }} 口</span>
          </template>
        </div>
        <span v-else style="color: #999">點上方 ETF 看明細</span>
      </template>
      <div ref="chartEl" style="width: 100%; height: 240px"></div>
      <el-tabs v-if="fund" v-model="tab">
        <el-tab-pane :label="`當日異動（${fund.flows.length}）`" name="flows">
          <el-table :data="fund.flows" stripe size="small" max-height="520" style="cursor: pointer"
                    @row-click="(r) => go(r.stock_id)">
            <el-table-column label="動作" width="96">
              <template #default="{ row }">
                <el-tag :type="ACT[row.action].type" :effect="ACT[row.action].effect" size="small">{{ ACT[row.action].label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="股票" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
            </el-table-column>
            <el-table-column prop="industry" label="產業" width="100" show-overflow-tooltip />
            <el-table-column label="前日→本日（張）" width="150" align="right">
              <template #default="{ row }">{{ lotsAbs(row.shares_prev) }} → {{ lotsAbs(row.shares) }}</template>
            </el-table-column>
            <el-table-column label="股數變化" width="100" align="right">
              <template #default="{ row }"><span :style="{ color: up(row.d_shares) }">{{ lots(row.d_shares) }}</span></template>
            </el-table-column>
            <el-table-column label="主動調整" width="100" align="right">
              <template #default="{ row }"><span :style="{ color: up(row.active_shares) }">{{ lots(row.active_shares) }}</span></template>
            </el-table-column>
            <el-table-column label="主動金額" width="96" align="right">
              <template #default="{ row }"><span :style="{ color: up(row.active_amount) }">{{ yi(row.active_amount) }}</span></template>
            </el-table-column>
            <el-table-column label="權重" width="130" align="right">
              <template #default="{ row }">{{ w(row.weight_prev) }} → {{ w(row.weight) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane :label="`持股明細（${fund.holdings.filter((h) => !h.dust).length}）`" name="holdings">
          <el-table :data="fund.holdings.filter((h) => !h.dust)" stripe size="small" max-height="520" style="cursor: pointer"
                    @row-click="(r) => go(r.stock_id)">
            <el-table-column label="股票" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">{{ row.stock_id }} {{ row.name }}</template>
            </el-table-column>
            <el-table-column prop="industry" label="產業" width="110" show-overflow-tooltip />
            <el-table-column label="張數" width="100" align="right">
              <template #default="{ row }">{{ lotsAbs(row.shares) }}</template>
            </el-table-column>
            <el-table-column label="權重" width="130">
              <template #default="{ row }">
                <el-progress :percentage="Math.min(100, Number(row.weight || 0) * 5)" :stroke-width="10"
                             :format="() => w(row.weight)" color="#e6a23c" />
              </template>
            </el-table-column>
            <el-table-column label="當日" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.action && row.action !== 'flow'" :type="ACT[row.action].type" :effect="ACT[row.action].effect"
                        size="small">{{ ACT[row.action].label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="主動調整" width="100" align="right">
              <template #default="{ row }">
                <span v-if="row.active_shares != null" :style="{ color: up(row.active_shares) }">{{ lots(row.active_shares) }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div style="color: #999; font-size: 12px; margin-top: 6px">
            不含佔位股（權重 0.00%，投信常在很多檔各留 1 張）。
          </div>
        </el-tab-pane>
      </el-tabs>
      <div v-if="fund" style="color: #999; font-size: 12px; margin-top: 8px">
        走勢圖：股票＋期貨＝經理人的整體持股水位；灰色柱為單位數（申購增加、贖回減少）。
        全面增減＝當天最多檔持股一起變動的比例（經理人全面等比例買賣時才會不是 0）；相對加碼＝全面等比例賣的那天賣得比別檔少。
      </div>
    </el-card>
  </div>
</template>

<style scoped>
:deep(.current-row-etf) td { background: #fdf6ec !important; }
</style>
