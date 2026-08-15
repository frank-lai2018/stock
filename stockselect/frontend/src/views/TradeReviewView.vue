<script setup>
// 交易復盤：把 trade_log 的已實現交易做橫切分析，回答「我靠什麼賺、又都怎麼賠」
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import * as XLSX from 'xlsx'
import { getTradeReview } from '../api'

const router = useRouter()
const data = ref({ count: 0, summary: null, cuts: {}, periods: { months: [], years: [] }, items: [] })
const loading = ref(false)
const cutKey = ref('hold')
const el = ref(null)
let chart = null

const UP = '#EA4C4C'
const DOWN = '#3F9E5A'
const CUTS = [
  { key: 'hold', name: '持有天數', hint: '抱多久的績效最好？賠錢的是不是抱太久？' },
  { key: 'momentum', name: '進場動能', hint: '買進當下「近 3 月漲跌」落在哪一段時，你做得最好' },
  { key: 'per', name: '進場本益比', hint: '買進當日 PER 落點；空白＝當天無估值資料（多為 ETF/虧損股）' },
  { key: 'ma60', name: '季線位置', hint: '買在季線上（追勢）vs 季線下（撿便宜），哪種適合你' },
  { key: 'industry', name: '產業', hint: '只列該產業≥3 筆的；看你在哪個產業有真本事' },
  { key: 'trade_type', name: '交易類別', hint: '現股 / 當沖 / 融資 的績效差異' },
]
const cutRows = computed(() => data.value.cuts?.[cutKey.value] || [])
const cutHint = computed(() => CUTS.find((c) => c.key === cutKey.value)?.hint || '')
const s = computed(() => data.value.summary)

const money = (v) => (v == null ? '—' : Math.round(v).toLocaleString('en-US'))
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%')
const pctR = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(2) + '%')
const clr = (v) => (v == null ? '' : Number(v) >= 0 ? UP : DOWN)

// 一句話結論：把最值得注意的兩三件事講白
const insights = computed(() => {
  const x = s.value
  if (!x) return []
  const out = []
  if (x.profit_factor != null) {
    out.push(x.profit_factor >= 1.5
      ? `獲利因子 ${x.profit_factor}：總獲利是總虧損的 ${x.profit_factor} 倍，這是一套會賺錢的系統`
      : `獲利因子 ${x.profit_factor}：${x.profit_factor > 1 ? '勉強為正，抗噪空間不大' : '長期為負，要先止血'}`)
  }
  if (x.avg_days_win != null && x.avg_days_loss != null) {
    out.push(x.avg_days_loss > x.avg_days_win
      ? `賺錢平均抱 ${x.avg_days_win} 天、賠錢卻抱 ${x.avg_days_loss} 天 — 典型的「賺的跑太快、賠的凹太久」，停損紀律是首要功課`
      : `賺錢平均抱 ${x.avg_days_win} 天、賠錢只抱 ${x.avg_days_loss} 天 — 停損比獲利了結果斷，這是好習慣`)
  }
  if (x.lose_avg_mfe != null) {
    out.push(`賠錢的交易中途平均曾浮盈 ${x.lose_avg_mfe}%${x.turned_loser ? `，其中 ${x.turned_loser} 筆曾賺超過 10% 最後仍收黑` : ''} — 考慮設移動停利`)
  }
  const worstCut = (data.value.cuts?.per || []).filter((r) => r.n >= 10)
    .sort((a, b) => (a.avg_ret ?? 0) - (b.avg_ret ?? 0))[0]
  if (worstCut && worstCut.avg_ret < 0) {
    out.push(`買在本益比「${worstCut.label}」的 ${worstCut.n} 筆，勝率只有 ${worstCut.win_rate}%、平均 ${pct(worstCut.avg_ret)} — 這段是你的破口`)
  }
  return out
})

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const m = data.value.periods.months
  chart.setOption({
    grid: { left: 72, right: 72, top: 34, bottom: 30 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : Math.round(v).toLocaleString('en-US')) },
    legend: { data: ['當月已實現', '累計'], top: 0 },
    xAxis: { type: 'category', data: m.map((r) => r.month) },
    yAxis: [{ type: 'value', name: '當月' }, { type: 'value', name: '累計' }],
    dataZoom: [{ type: 'inside' }],
    series: [
      { name: '當月已實現', type: 'bar',
        data: m.map((r) => ({ value: r.pnl, itemStyle: { color: r.pnl >= 0 ? UP : DOWN } })) },
      { name: '累計', type: 'line', yAxisIndex: 1, showSymbol: false, smooth: true,
        data: m.map((r) => r.cum), lineStyle: { color: '#2E7DEE', width: 2 }, itemStyle: { color: '#2E7DEE' } },
    ],
  })
}

async function load() {
  loading.value = true
  try {
    data.value = await getTradeReview()
    await nextTick()
    render()
  } catch (e) {
    ElMessage.error('載入失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
function onResize() { if (chart) chart.resize() }
onMounted(() => { load(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); if (chart) chart.dispose() })

function go(row) { router.push(`/stock/${row.stock_id}`) }

function downloadXlsx() {
  if (!data.value.items.length) return ElMessage.warning('沒有資料可下載')
  const cols = [
    ['代碼', (r) => r.stock_id], ['名稱', (r) => r.name], ['產業', (r) => r.industry],
    ['類別', (r) => r.trade_type], ['買進日', (r) => r.buy_date], ['賣出日', (r) => r.sell_date],
    ['持有天數', (r) => r.days], ['股數', (r) => r.shares],
    ['買價', (r) => r.buy_price], ['賣價', (r) => r.sell_price],
    ['報酬%', (r) => (r.ret_pct == null ? '' : Number((r.ret_pct * 100).toFixed(2)))],
    ['損益', (r) => r.pnl],
    ['進場近3月%', (r) => (r.entry_ret_3m == null ? '' : Number((r.entry_ret_3m * 100).toFixed(2)))],
    ['進場站季線', (r) => (r.entry_above_ma60 == null ? '' : (r.entry_above_ma60 ? 'Y' : 'N'))],
    ['進場PER', (r) => r.entry_per],
    ['最大浮盈%', (r) => (r.mfe == null ? '' : Number((r.mfe * 100).toFixed(2)))],
    ['最大浮虧%', (r) => (r.mae == null ? '' : Number((r.mae * 100).toFixed(2)))],
  ]
  const aoa = [cols.map((c) => c[0])]
  for (const r of data.value.items) aoa.push(cols.map((c) => { const v = c[1](r); return v == null ? '' : v }))
  const ws = XLSX.utils.aoa_to_sheet(aoa)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '交易復盤')
  XLSX.writeFile(wb, 'trade_review.xlsx')
}
</script>

<template>
  <div v-loading="loading">
    <el-empty v-if="!loading && !data.count" description="尚無已實現交易（trade_log 需要有配對完成的買賣）" />

    <template v-if="data.count">
      <!-- KPI -->
      <div style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px">
        <el-card shadow="never" style="flex: 1 1 190px">
          <div style="color: #909399; font-size: 13px">已實現總損益（{{ data.count }} 筆平倉）</div>
          <div style="font-size: 24px; font-weight: 700" :style="{ color: clr(s.total_pnl) }">{{ money(s.total_pnl) }}</div>
          <div style="color: #909399; font-size: 12px">賺 {{ money(s.gross_win) }}｜賠 {{ money(-s.gross_loss) }}</div>
        </el-card>
        <el-card shadow="never" style="flex: 1 1 190px">
          <div style="color: #909399; font-size: 13px">勝率</div>
          <div style="font-size: 24px; font-weight: 700">{{ s.win_rate }}%</div>
          <div style="color: #909399; font-size: 12px">{{ s.wins }} 勝 / {{ s.losses }} 敗</div>
        </el-card>
        <el-card shadow="never" style="flex: 1 1 190px">
          <div style="color: #909399; font-size: 13px">賺賠比（平均獲利 ÷ 平均虧損）</div>
          <div style="font-size: 24px; font-weight: 700">{{ s.payoff ?? '—' }}</div>
          <div style="color: #909399; font-size: 12px">
            賺 <b :style="{ color: UP }">{{ pct(s.avg_win_pct) }}</b>／賠 <b :style="{ color: DOWN }">{{ pct(s.avg_loss_pct) }}</b>
          </div>
        </el-card>
        <el-card shadow="never" style="flex: 1 1 190px">
          <div style="color: #909399; font-size: 13px">獲利因子</div>
          <div style="font-size: 24px; font-weight: 700" :style="{ color: s.profit_factor >= 1 ? UP : DOWN }">
            {{ s.profit_factor ?? '—' }}
          </div>
          <div style="color: #909399; font-size: 12px">＞1.5 屬穩健、＜1 長期虧損</div>
        </el-card>
        <el-card shadow="never" style="flex: 1 1 190px">
          <div style="color: #909399; font-size: 13px">每筆期望值</div>
          <div style="font-size: 24px; font-weight: 700" :style="{ color: clr(s.expectancy_pct) }">{{ pct(s.expectancy_pct) }}</div>
          <div style="color: #909399; font-size: 12px">最長連勝 {{ s.max_consec_win }}／連敗 {{ s.max_consec_loss }}</div>
        </el-card>
      </div>

      <!-- 白話結論 -->
      <el-card shadow="never" style="margin-bottom: 12px" header="📌 這些數字在說什麼">
        <ul style="margin: 0; padding-left: 20px; line-height: 1.9">
          <li v-for="(t, i) in insights" :key="i">{{ t }}</li>
        </ul>
        <div style="margin-top: 8px; color: #909399; font-size: 12px">
          最大浮盈/浮虧＝持有期間相對買進價的最高/最低點，用未還原價計算，對得上你實際成交價；
          進場情境（動能／季線／本益比）一律用<b>買進日當下</b>的資料重算，不含未來資訊
        </div>
      </el-card>

      <!-- 月度損益 + 累計 -->
      <el-card shadow="never" style="margin-bottom: 12px" header="已實現損益（依賣出月份）">
        <div ref="el" style="width: 100%; height: 300px"></div>
        <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px">
          <el-tag v-for="y in data.periods.years" :key="y.year"
                  :type="y.pnl >= 0 ? 'danger' : 'success'" effect="plain">
            {{ y.year }}：{{ money(y.pnl) }}（{{ y.n }} 筆・勝率 {{ y.win_rate }}%）
          </el-tag>
        </div>
      </el-card>

      <!-- 分組績效 -->
      <el-card shadow="never" style="margin-bottom: 12px">
        <template #header>
          <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
            <b>分組績效</b>
            <el-radio-group v-model="cutKey" size="small">
              <el-radio-button v-for="c in CUTS" :key="c.key" :value="c.key">{{ c.name }}</el-radio-button>
            </el-radio-group>
            <span style="color: #909399; font-size: 12px">{{ cutHint }}</span>
          </div>
        </template>
        <el-table :data="cutRows" stripe size="small">
          <el-table-column prop="label" label="分組" width="180" />
          <el-table-column prop="n" label="筆數" width="90" align="right" sortable />
          <el-table-column label="勝率" width="110" align="right" sortable :sort-method="(a, b) => a.win_rate - b.win_rate">
            <template #default="{ row }">
              <span :style="{ color: row.win_rate >= 60 ? UP : row.win_rate < 45 ? DOWN : '' }">{{ row.win_rate }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="平均報酬" width="120" align="right" sortable :sort-method="(a, b) => (a.avg_ret ?? 0) - (b.avg_ret ?? 0)">
            <template #default="{ row }"><span :style="{ color: clr(row.avg_ret) }">{{ pct(row.avg_ret) }}</span></template>
          </el-table-column>
          <el-table-column label="平均持有" width="110" align="right">
            <template #default="{ row }">{{ row.avg_days }} 天</template>
          </el-table-column>
          <el-table-column label="總損益" align="right" sortable :sort-method="(a, b) => a.pnl - b.pnl">
            <template #default="{ row }"><span :style="{ color: clr(row.pnl) }">{{ money(row.pnl) }}</span></template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 明細 -->
      <el-card shadow="never">
        <template #header>
          <div style="display: flex; align-items: center; gap: 12px">
            <b>平倉明細（{{ data.count }} 筆）</b>
            <el-button size="small" type="success" @click="downloadXlsx">⬇ 下載 Excel</el-button>
            <span style="color: #909399; font-size: 12px">點列看該股 K 線</span>
          </div>
        </template>
        <el-table :data="data.items" stripe size="small" style="cursor: pointer" @row-click="go">
          <el-table-column prop="stock_id" label="代碼" width="76" fixed />
          <el-table-column prop="name" label="名稱" width="100" fixed />
          <el-table-column prop="buy_date" label="買進" width="106" sortable />
          <el-table-column prop="sell_date" label="賣出" width="106" sortable />
          <el-table-column prop="days" label="持有" width="84" align="right" sortable>
            <template #default="{ row }">{{ row.days }} 天</template>
          </el-table-column>
          <el-table-column label="報酬" width="96" align="right" sortable :sort-method="(a, b) => a.ret_pct - b.ret_pct">
            <template #default="{ row }"><span :style="{ color: clr(row.ret_pct) }">{{ pctR(row.ret_pct) }}</span></template>
          </el-table-column>
          <el-table-column label="損益" width="110" align="right" sortable :sort-method="(a, b) => a.pnl - b.pnl">
            <template #default="{ row }"><span :style="{ color: clr(row.pnl) }">{{ money(row.pnl) }}</span></template>
          </el-table-column>
          <el-table-column label="最大浮盈" width="100" align="right">
            <template #default="{ row }"><span :style="{ color: UP }">{{ pctR(row.mfe) }}</span></template>
          </el-table-column>
          <el-table-column label="最大浮虧" width="100" align="right">
            <template #default="{ row }"><span :style="{ color: DOWN }">{{ pctR(row.mae) }}</span></template>
          </el-table-column>
          <el-table-column label="進場近3月" width="110" align="right">
            <template #default="{ row }"><span :style="{ color: clr(row.entry_ret_3m) }">{{ pctR(row.entry_ret_3m) }}</span></template>
          </el-table-column>
          <el-table-column label="進場季線" width="94">
            <template #default="{ row }">
              <el-tag v-if="row.entry_above_ma60 === true" size="small" type="danger" effect="plain">季線上</el-tag>
              <el-tag v-else-if="row.entry_above_ma60 === false" size="small" type="success" effect="plain">季線下</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="進場PER" width="94" align="right">
            <template #default="{ row }">{{ row.entry_per ?? '—' }}</template>
          </el-table-column>
          <el-table-column prop="trade_type" label="類別" width="100" />
          <el-table-column prop="industry" label="產業" min-width="120" show-overflow-tooltip />
        </el-table>
      </el-card>
    </template>
  </div>
</template>
