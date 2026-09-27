<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getMarketOverview, getMarketIndex, getMovers, getSectors, getMoneyflow, getMarketMargin,
         getDrawingAlerts, getThemeToday } from '../api'

const router = useRouter()
async function loadAlerts() {
  try { alerts.value = await getDrawingAlerts(0.02) } catch (e) { /* 沒畫線或後端舊版 → 不顯示 */ }
}

// 今日族群熱度（nightly 的 theme 工作產生；完整排行／熱力圖在 /themes）
const themeToday = ref(null)
async function loadThemeToday() {
  try { themeToday.value = await getThemeToday(10) } catch (e) { /* 族群表未建或尚未算熱度 → 不顯示 */ }
}
const pctf = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(1) + '%')   // 小數 → %
function openTheme(code, layer = 3) { router.push({ path: '/themes', query: { code, layer } }) }
const ov = ref(null)
const moverType = ref('gainers')
const movers = ref([])
const sectorMarket = ref('上市')
const sectors = ref([])
const flow = ref([])
const marginMarket = ref('ALL')                   // 大盤信用交易：ALL 合計 / TWSE 上市 / TPEx 上櫃
const marginRows = ref([])
// 手繪線警報：把個股圖上畫的趨勢線/水平線延伸到今天，比對收盤有沒有穿越
const alerts = ref({ count: 0, as_of: null, items: [] })
const SIG = {
  break_down: { type: 'success', label: '跌破' },      // 綠＝偏空（台股慣例）
  break_up: { type: 'danger', label: '站上' },
  near: { type: 'warning', label: '接近' },
}
const chartEl = ref(null)
let chart = null
const idxSel = ref('TWSE')                        // 走勢圖選擇的指數（TWSE=加權股價指數）
const IDX_NAME = { TWSE: '加權指數', TPEx: '櫃買指數' }

async function loadIndex() {
  renderChart(await getMarketIndex(120, idxSel.value))
}

const yi = (v) => (v == null ? '—' : (Number(v) / 1e8).toFixed(0) + ' 億')
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%')
const up = (v) => (v >= 0 ? '#EA4C4C' : '#3F9E5A')
const wanLot = (v) => (v == null ? '—' : (Number(v) / 1e4).toFixed(1) + ' 萬張')          // 張→萬張
const wanLotChg = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) / 1e4).toFixed(1) + ' 萬張')

async function loadMovers() {
  movers.value = await getMovers(moverType.value, 15)
}

async function loadSectors() {
  sectors.value = await getSectors(sectorMarket.value)
  flow.value = await getMoneyflow(sectorMarket.value)
}

async function loadMargin() {
  marginRows.value = await getMarketMargin(20, marginMarket.value)
}
const lots = (v) => (v == null ? '—' : Number(v).toLocaleString('en-US'))
const lotsChg = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toLocaleString('en-US'))
const yiChg = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2))

function renderChart(rows) {
  if (!chart) chart = echarts.init(chartEl.value)
  const rise = rows.length > 1 && +rows[rows.length - 1].close >= +rows[0].close   // 期間漲跌決定顏色
  const col = rise ? '234,76,76' : '63,158,90'                                       // 紅漲綠跌
  chart.setOption({
    grid: { left: 64, right: 20, top: 20, bottom: 30 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', boundaryGap: false, data: rows.map((r) => String(r.trade_date).slice(0, 10)) },
    yAxis: { type: 'value', scale: true },
    series: [{
      type: 'line', showSymbol: false, data: rows.map((r) => +r.close),
      lineStyle: { color: `rgb(${col})`, width: 2 },
      areaStyle: { color: `rgba(${col},0.08)` },
    }],
  }, true)
}
function onResize() { if (chart) chart.resize() }

onMounted(async () => {
  loadThemeToday()                        // 獨立載入：大盤其他區塊失敗也不影響族群卡片
  try {
    ov.value = await getMarketOverview()
    await loadIndex()
    await loadMovers()
    await loadSectors()
    await loadMargin()
    await loadAlerts()
  } catch (e) {
    ElMessage.error('載入大盤失敗：' + (e?.response?.data?.detail || e.message))
  }
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) chart.dispose()
})

function go(id) { router.push(`/stock/${id}`) }
</script>

<template>
  <div>
    <div style="display: flex; gap: 16px; flex-wrap: wrap">
      <el-card shadow="never" style="flex: 1 1 260px">
        <div style="color: #999">加權指數</div>
        <template v-if="ov?.taiex">
          <div style="font-size: 30px; font-weight: 700" :style="{ color: up(ov.taiex.change) }">
            {{ ov.taiex.close.toFixed(2) }}
          </div>
          <div :style="{ color: up(ov.taiex.change) }">
            {{ ov.taiex.change >= 0 ? '▲' : '▼' }}
            {{ ov.taiex.change != null ? ov.taiex.change.toFixed(2) : '—' }} ({{ pct(ov.taiex.pct) }})
          </div>
        </template>
        <div style="color: #999; font-size: 12px; margin-top: 6px">資料日 {{ ov?.as_of || '—' }}</div>
      </el-card>

      <el-card shadow="never" style="flex: 1 1 260px">
        <div style="color: #999">櫃買指數 TPEx</div>
        <template v-if="ov?.tpex">
          <div style="font-size: 30px; font-weight: 700" :style="{ color: up(ov.tpex.change) }">
            {{ ov.tpex.close.toFixed(2) }}
          </div>
          <div :style="{ color: up(ov.tpex.change) }">
            {{ ov.tpex.change >= 0 ? '▲' : '▼' }}
            {{ ov.tpex.change != null ? ov.tpex.change.toFixed(2) : '—' }} ({{ pct(ov.tpex.pct) }})
          </div>
        </template>
        <div style="color: #999; font-size: 12px; margin-top: 6px">資料日 {{ ov?.tpex?.date || '—' }}</div>
      </el-card>

      <el-card shadow="never" style="flex: 1 1 260px">
        <div style="color: #999">漲跌家數</div>
        <div style="font-size: 20px; margin-top: 6px">
          <span style="color: #EA4C4C">▲ {{ ov?.breadth?.up ?? '—' }}</span>
          <span style="margin: 0 12px; color: #999">平 {{ ov?.breadth?.flat ?? '—' }}</span>
          <span style="color: #3F9E5A">▼ {{ ov?.breadth?.down ?? '—' }}</span>
        </div>
        <div style="color: #999; font-size: 12px; margin-top: 10px">
          全市場總成交值 {{ yi(ov?.total_amount) }}
        </div>
      </el-card>

      <el-card shadow="never" style="flex: 1 1 260px">
        <div style="color: #999">大盤融資餘額</div>
        <template v-if="ov?.margin">
          <div style="font-size: 30px; font-weight: 700">{{ wanLot(ov.margin.balance) }}</div>
          <div :style="{ color: up(ov.margin.change) }">
            {{ ov.margin.change >= 0 ? '▲' : '▼' }}
            {{ wanLotChg(ov.margin.change) }} ({{ pct(ov.margin.pct) }})
          </div>
          <div style="color: #999; font-size: 12px; margin-top: 6px">融資資料日 {{ ov.margin.date }}</div>
        </template>
        <template v-else><div style="font-size: 20px; margin-top: 6px">—</div></template>
      </el-card>
    </div>

    <!-- 手繪線警報：只有真的觸發才顯示，沒畫線就完全不佔版面 -->
    <el-card v-if="alerts.count" shadow="never" style="margin-top: 16px">
      <template #header>
        <span style="font-weight: 600">✏ 手繪線警報</span>
        <el-tag type="danger" effect="dark" size="small" style="margin-left: 8px">{{ alerts.count }} 筆</el-tag>
        <span style="color: #999; font-size: 12px; margin-left: 8px">
          你在個股 K 線上畫的線延伸到 {{ alerts.as_of }}，與收盤比對；點列看圖
        </span>
      </template>
      <el-table :data="alerts.items" size="small" stripe style="cursor: pointer"
                @row-click="(r) => router.push(`/stock/${r.stock_id}`)">
        <el-table-column label="訊號" width="86">
          <template #default="{ row }">
            <el-tag :type="SIG[row.signal].type" size="small" effect="dark">{{ SIG[row.signal].label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stock_id" label="代碼" width="76" />
        <el-table-column prop="name" label="名稱" width="110" />
        <el-table-column prop="text" label="說明" min-width="220" />
        <el-table-column label="收盤" width="90" align="right"><template #default="{ row }">{{ row.close }}</template></el-table-column>
        <el-table-column label="線價位" width="90" align="right"><template #default="{ row }">{{ row.line }}</template></el-table-column>
        <el-table-column label="距線" width="90" align="right">
          <template #default="{ row }">
            <span :style="{ color: row.gap >= 0 ? '#EA4C4C' : '#3F9E5A' }">
              {{ row.gap == null ? '—' : (row.gap >= 0 ? '+' : '') + (row.gap * 100).toFixed(2) + '%' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="線別" width="150">
          <template #default="{ row }">
            <span style="color: #666">{{ row.tool_name }}</span>
            <span style="color: #bbb; font-size: 12px; margin-left: 4px">{{ row.period }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="備註" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 今日族群熱度：同 nightly log 的族群排行段落；點題材或個股可深入 -->
    <el-card v-if="themeToday && themeToday.rows.length" shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px">
          <span style="font-weight: 600">今日族群熱度</span>
          <span style="color: #999; font-size: 12px">
            市場題材｜資料日 {{ themeToday.as_of }}｜名次變化對照 {{ themeToday.prev }}｜點列看成分股
          </span>
          <el-button link type="primary" style="margin-left: auto" @click="router.push('/themes')">完整排行與熱力圖 →</el-button>
        </div>
      </template>
      <div style="display: flex; gap: 20px; flex-wrap: wrap">
        <el-table :data="themeToday.rows" size="small" style="flex: 3 1 520px; cursor: pointer"
                  @row-click="(r) => openTheme(r.code)">
          <el-table-column label="名次" width="54" align="center">
            <template #default="{ row }"><b>{{ row.heat_rank }}</b></template>
          </el-table-column>
          <el-table-column label="變化" width="58" align="center">
            <template #default="{ row }">
              <span v-if="row.rank_chg > 0" style="color: #EA4C4C">▲{{ row.rank_chg }}</span>
              <span v-else-if="row.rank_chg < 0" style="color: #3F9E5A">▼{{ -row.rank_chg }}</span>
              <span v-else style="color: #999">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="題材" min-width="170" show-overflow-tooltip />
          <el-table-column label="5日" width="74" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.ret_5d) }">{{ pctf(row.ret_5d) }}</span></template>
          </el-table-column>
          <el-table-column label="20日" width="78" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.ret_20d) }">{{ pctf(row.ret_20d) }}</span></template>
          </el-table-column>
          <el-table-column label="站上月線" width="80" align="right">
            <template #default="{ row }">{{ Math.round((row.breadth_ma20 || 0) * 100) }}%</template>
          </el-table-column>
          <el-table-column label="法人" width="72" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.inst_ratio) }">{{ pctf(row.inst_ratio) }}</span></template>
          </el-table-column>
          <el-table-column label="熱度" width="64" align="right">
            <template #default="{ row }"><b>{{ Number(row.heat_score).toFixed(0) }}</b></template>
          </el-table-column>
        </el-table>

        <div style="flex: 2 1 340px; font-size: 13px">
          <div v-for="s in themeToday.spotlight" :key="s.code" style="margin-bottom: 14px">
            <div style="font-weight: 600; margin-bottom: 4px; cursor: pointer" @click="openTheme(s.code)">{{ s.name }}</div>
            <div style="line-height: 2">
              <el-tag size="small" type="danger" effect="plain">領頭羊</el-tag>
              <span v-for="x in s.leaders" :key="x.stock_id" style="margin-left: 10px; cursor: pointer" @click="go(x.stock_id)">
                {{ x.name }} <span :style="{ color: up(x.ret_20d) }">{{ pctf(x.ret_20d) }}</span>
              </span>
            </div>
            <div style="line-height: 2">
              <el-tag size="small" type="warning" effect="plain">落後補漲</el-tag>
              <span v-for="x in s.laggards" :key="x.stock_id" style="margin-left: 10px; cursor: pointer" @click="go(x.stock_id)">
                {{ x.name }} <span :style="{ color: up(x.ret_20d) }">{{ pctf(x.ret_20d) }}</span>
              </span>
              <span v-if="!s.laggards.length" style="margin-left: 10px; color: #999">（無：落後者都已跌破季線）</span>
            </div>
          </div>
          <div v-if="themeToday.nodes.length" style="color: #666; border-top: 1px dashed #e5e5e5; padding-top: 8px; line-height: 1.9">
            <span style="color: #999">產業鏈雷達</span>
            <div v-for="n in themeToday.nodes" :key="n.code" style="cursor: pointer" @click="openTheme(n.code, 2)">
              #{{ n.heat_rank }} {{ n.name }}
              <span :style="{ color: up(n.ret_20d) }">20日 {{ pctf(n.ret_20d) }}</span>
            </div>
          </div>
          <div style="color: #bbb; font-size: 12px; margin-top: 6px">
            落後補漲＝20 日報酬低於族群中位數、但仍站上季線。
          </div>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        大盤信用交易
        <el-radio-group v-model="marginMarket" size="small" style="margin-left: 12px" @change="loadMargin">
          <el-radio-button value="ALL">合計</el-radio-button>
          <el-radio-button value="TWSE">上市</el-radio-button>
          <el-radio-button value="TPEx">上櫃</el-radio-button>
        </el-radio-group>
        <span style="margin-left: 10px; color: #999; font-size: 12px">融資餘額(億)＋增減、融券餘額(張)＋增減；維持率為自算約值</span>
      </template>
      <el-table :data="marginRows" stripe height="360" size="small">
        <el-table-column prop="date" label="日期" width="110" />
        <el-table-column label="融資餘額(億)" width="120">
          <template #default="{ row }">{{ row.margin_yi != null ? row.margin_yi.toFixed(2) : '—' }}</template>
        </el-table-column>
        <el-table-column label="融資增減(億)" width="120">
          <template #default="{ row }"><span :style="{ color: up(row.margin_chg_yi) }">{{ yiChg(row.margin_chg_yi) }}</span></template>
        </el-table-column>
        <el-table-column label="維持率" width="100">
          <template #default="{ row }">{{ row.maint_ratio != null ? row.maint_ratio.toFixed(2) + '%' : '—' }}</template>
        </el-table-column>
        <el-table-column label="融券餘額(張)" width="130">
          <template #default="{ row }">{{ lots(row.short_lots) }}</template>
        </el-table-column>
        <el-table-column label="融券增減(張)" min-width="120">
          <template #default="{ row }"><span :style="{ color: up(row.short_chg) }">{{ lotsChg(row.short_chg) }}</span></template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        指數走勢（近 120 交易日）
        <el-radio-group v-model="idxSel" size="small" style="margin-left: 12px" @change="loadIndex">
          <el-radio-button value="TWSE">加權指數</el-radio-button>
          <el-radio-button value="TPEx">櫃買指數</el-radio-button>
        </el-radio-group>
      </template>
      <div ref="chartEl" style="width: 100%; height: 320px"></div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <el-radio-group v-model="moverType" size="small" @change="loadMovers">
          <el-radio-button value="gainers">漲幅榜</el-radio-button>
          <el-radio-button value="losers">跌幅榜</el-radio-button>
          <el-radio-button value="active">成交值榜</el-radio-button>
        </el-radio-group>
        <span style="margin-left: 12px; color: #999; font-size: 12px">日均額 ≥ 2000 萬｜點列看個股</span>
      </template>
      <el-table :data="movers" stripe height="360" style="cursor: pointer" @row-click="(r) => go(r.stock_id)">
        <el-table-column prop="stock_id" label="代碼" width="80" />
        <el-table-column prop="name" label="名稱" width="120" />
        <el-table-column prop="industry" label="產業" width="140" show-overflow-tooltip />
        <el-table-column label="收盤" width="100"><template #default="{ row }">{{ row.close }}</template></el-table-column>
        <el-table-column label="漲跌幅" width="110">
          <template #default="{ row }"><span :style="{ color: up(row.chg_pct) }">{{ pct(row.chg_pct) }}</span></template>
        </el-table-column>
        <el-table-column label="成交值"><template #default="{ row }">{{ yi(row.amount) }}</template></el-table-column>
      </el-table>
    </el-card>

    <div style="display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px">
      <el-card shadow="never" style="flex: 2 1 460px">
        <template #header>
          類股漲跌
          <el-radio-group v-model="sectorMarket" size="small" style="margin-left: 12px" @change="loadSectors">
            <el-radio-button value="上市">上市</el-radio-button>
            <el-radio-button value="上櫃">上櫃</el-radio-button>
          </el-radio-group>
          <span style="margin-left: 10px; color: #999; font-size: 12px">成員股等權平均｜點列看代表股</span>
        </template>
        <el-table :data="sectors" height="380" stripe style="cursor: pointer" @row-click="(r) => go(r.top_id)">
          <el-table-column prop="industry" label="產業" width="150" show-overflow-tooltip />
          <el-table-column label="平均漲跌" width="110">
            <template #default="{ row }"><span :style="{ color: up(row.avg_pct) }">{{ pct(row.avg_pct) }}</span></template>
          </el-table-column>
          <el-table-column label="檔數" width="70" prop="n" />
          <el-table-column label="代表股">
            <template #default="{ row }">
              {{ row.top_id }} {{ row.top_name }}
              <span :style="{ color: up(row.top_pct) }">&nbsp;{{ pct(row.top_pct) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never" style="flex: 1 1 320px" header="資金流向（成交值佔比）">
        <el-table :data="flow" height="380" stripe>
          <el-table-column prop="industry" label="產業" width="140" show-overflow-tooltip />
          <el-table-column label="佔比">
            <template #default="{ row }">
              <el-progress :percentage="Math.min(Number(row.share_pct) || 0, 100)" :stroke-width="12" :show-text="false" />
              <span style="font-size: 12px; color: #666">{{ row.share_pct }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="較前一日" width="100">
            <template #default="{ row }"><span :style="{ color: up(row.chg_pct) }">{{ pct(row.chg_pct) }}</span></template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>
