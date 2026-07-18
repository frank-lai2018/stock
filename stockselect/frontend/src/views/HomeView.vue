<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getMarketOverview, getMarketIndex, getMovers, getSectors, getMoneyflow } from '../api'

const router = useRouter()
const ov = ref(null)
const moverType = ref('gainers')
const movers = ref([])
const sectorMarket = ref('上市')
const sectors = ref([])
const flow = ref([])
const chartEl = ref(null)
let chart = null
const idxSel = ref('TAIEX')                       // 走勢圖選擇的指數
const IDX_NAME = { TAIEX: '加權指數', TPEx: '櫃買指數' }

async function loadIndex() {
  renderChart(await getMarketIndex(120, idxSel.value))
}

const yi = (v) => (v == null ? '—' : (Number(v) / 1e8).toFixed(0) + ' 億')
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%')
const up = (v) => (v >= 0 ? '#EA4C4C' : '#3F9E5A')

async function loadMovers() {
  movers.value = await getMovers(moverType.value, 15)
}

async function loadSectors() {
  sectors.value = await getSectors(sectorMarket.value)
  flow.value = await getMoneyflow(sectorMarket.value)
}

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
  try {
    ov.value = await getMarketOverview()
    await loadIndex()
    await loadMovers()
    await loadSectors()
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
        <div style="color: #999">加權指數 TAIEX</div>
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
    </div>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        指數走勢（近 120 交易日）
        <el-radio-group v-model="idxSel" size="small" style="margin-left: 12px" @change="loadIndex">
          <el-radio-button value="TAIEX">加權指數</el-radio-button>
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
