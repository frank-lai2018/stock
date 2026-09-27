<script setup>
// 族群熱度：L3 市場題材 / L2 櫃買產業鏈節點的熱度排行、輪動熱力圖、成分明細（領頭羊 vs 落後補漲）
// 資料由 build_theme_daily.py 每晚產生（nightly 的 theme 工作）；說明見 族群分類設計.md
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getThemeRanking, getThemeHeatmap, getThemeMembers } from '../api'

const route = useRoute()
const router = useRouter()
const layer = ref(Number(route.query.layer) === 2 ? 2 : 3)   // 3=市場題材　2=產業鏈節點；個股頁點族群標籤會帶 ?code=&layer=
const metric = ref('heat_score')
const ranking = ref({ as_of: null, prev: null, rows: [] })
const detail = ref(null)
const loading = ref(false)
const detailLoading = ref(false)
const heatEl = ref(null)
const heatHeight = ref(300)
let chart = null

const METRICS = {
  heat_score: { label: '熱度分數', range: [0, 100], fmt: (v) => v.toFixed(0) },
  ret_5d: { label: '5 日報酬', range: [-0.12, 0.12], fmt: (v) => pctx(v) },
  ret_20d: { label: '20 日報酬', range: [-0.25, 0.25], fmt: (v) => pctx(v) },
  breadth_ma20: { label: '站上月線比例', range: [0, 1], fmt: (v) => (v * 100).toFixed(0) + '%' },
}
const TAG = { leader: { type: 'danger', label: '領頭羊' }, laggard: { type: 'warning', label: '落後補漲' } }
const STATUS = { confirmed: '已確認', seed: '待複核' }

const pctx = (v, d = 1) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(d) + '%')
const up = (v) => (v == null ? '#999' : v >= 0 ? '#EA4C4C' : '#3F9E5A')
const yi = (v) => (v == null ? '—' : (Number(v) / 1e8).toFixed(1) + ' 億')
const lots = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(Number(v) / 1000).toLocaleString('en-US') + ' 張')

async function load(code) {
  loading.value = true
  try {
    ranking.value = await getThemeRanking(layer.value, layer.value === 3 ? 50 : 40)
    await loadHeat()
    const target = code || ranking.value.rows[0]?.code
    if (target) await openTheme(target)
    else detail.value = null
  } catch (e) {
    ElMessage.error('載入族群熱度失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadHeat() {
  const h = await getThemeHeatmap(layer.value, { days: 20, top: layer.value === 3 ? 20 : 25, metric: metric.value })
  heatHeight.value = Math.max(220, h.themes.length * 24 + 70)
  await nextTick()
  render(h)
}

function render(h) {
  if (!heatEl.value) return
  if (!chart) chart = echarts.init(heatEl.value)
  else chart.resize()                                        // 族群數變了 → 容器高度變了
  const m = METRICS[metric.value]
  const n = h.themes.length
  const codes = h.themes.map((t) => t.code).reverse()       // 第 1 名放最上面
  const names = h.themes.map((t) => t.name).reverse()
  const cells = h.cells.map(([x, y, v]) => [x, n - 1 - y, v])
  chart.off('click')                                         // 每次重畫換成這次的族群清單
  chart.on('click', (p) => { if (p.data) openTheme(codes[p.data[1]]) })
  chart.setOption({
    grid: { left: 8, right: 16, top: 8, bottom: 56, containLabel: true },
    tooltip: {
      formatter: (p) => `${names[p.data[1]]}<br/>${h.dates[p.data[0]]}　${m.label}：<b>${p.data[2] == null ? '—' : m.fmt(p.data[2])}</b>`,
    },
    xAxis: { type: 'category', data: h.dates.map((d) => d.slice(5)), splitArea: { show: true } },
    yAxis: { type: 'category', data: names, axisLabel: { width: 190, overflow: 'truncate' } },
    visualMap: {
      min: m.range[0], max: m.range[1], calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
      itemHeight: 160, precision: metric.value === 'heat_score' ? 0 : 2,
      inRange: { color: ['#3F9E5A', '#f4f4f4', '#EA4C4C'] },          // 綠冷紅熱（台股慣例）
    },
    series: [{ type: 'heatmap', data: cells, emphasis: { itemStyle: { borderColor: '#333', borderWidth: 1 } } }],
  }, true)
}

async function openTheme(code) {
  if (!code) return
  detailLoading.value = true
  try {
    detail.value = await getThemeMembers(code)
  } catch (e) {
    ElMessage.error('載入族群成分失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    detailLoading.value = false
  }
}

function switchLayer() { detail.value = null; load() }
function rowClass({ row }) { return detail.value && row.code === detail.value.code ? 'current-theme' : '' }
function onResize() { if (chart) chart.resize() }
function go(id) { router.push(`/stock/${id}`) }

onMounted(async () => {
  await load(route.query.code)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) chart.dispose()
})
</script>

<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 12px">
          <b>族群熱度</b>
          <el-radio-group v-model="layer" size="small" @change="switchLayer">
            <el-radio-button :value="3">市場題材</el-radio-button>
            <el-radio-button :value="2">產業鏈節點</el-radio-button>
          </el-radio-group>
          <span style="color: #999; font-size: 12px">
            資料日 {{ ranking.as_of || '—' }}｜名次變化對照 {{ ranking.prev || '—' }}｜點列看成分股
          </span>
        </div>
      </template>
      <el-table :data="ranking.rows" v-loading="loading" stripe height="420" :row-class-name="rowClass"
                style="cursor: pointer" @row-click="(r) => openTheme(r.code)">
        <el-table-column label="名次" width="62" align="center">
          <template #default="{ row }"><b>{{ row.heat_rank }}</b></template>
        </el-table-column>
        <el-table-column label="變化" width="66" align="center">
          <template #default="{ row }">
            <span v-if="row.rank_chg == null" style="color: #999">新</span>
            <span v-else-if="row.rank_chg > 0" style="color: #EA4C4C">▲{{ row.rank_chg }}</span>
            <span v-else-if="row.rank_chg < 0" style="color: #3F9E5A">▼{{ -row.rank_chg }}</span>
            <span v-else style="color: #999">—</span>
          </template>
        </el-table-column>
        <el-table-column label="族群" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.name }}
            <el-tooltip v-if="row.n_seed > 0" :content="`含 ${row.n_seed} 檔起手名單尚未複核`">
              <span style="color: #e6a23c">＊</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="n_members" label="檔數" width="62" align="right" />
        <el-table-column label="1日" width="80" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.ret_1d) }">{{ pctx(row.ret_1d) }}</span></template>
        </el-table-column>
        <el-table-column label="5日" width="80" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.ret_5d) }">{{ pctx(row.ret_5d) }}</span></template>
        </el-table-column>
        <el-table-column label="20日" width="84" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.ret_20d) }">{{ pctx(row.ret_20d) }}</span></template>
        </el-table-column>
        <el-table-column label="站上月線" width="120">
          <template #default="{ row }">
            <el-progress :percentage="Math.round((row.breadth_ma20 || 0) * 100)" :stroke-width="10"
                         :color="(row.breadth_ma20 || 0) >= 0.5 ? '#EA4C4C' : '#3F9E5A'" />
          </template>
        </el-table-column>
        <el-table-column label="量比" width="66" align="right">
          <template #default="{ row }">
            <span :style="{ color: row.vol_ratio >= 1.2 ? '#EA4C4C' : row.vol_ratio < 0.8 ? '#3F9E5A' : '' }">
              {{ row.vol_ratio == null ? '—' : Number(row.vol_ratio).toFixed(2) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="法人" width="76" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.inst_ratio) }">{{ pctx(row.inst_ratio) }}</span></template>
        </el-table-column>
        <el-table-column label="熱度" width="130">
          <template #default="{ row }">
            <el-progress :percentage="Math.round(row.heat_score || 0)" :stroke-width="10" :format="(p) => p"
                         :color="[{ color: '#3F9E5A', percentage: 40 }, { color: '#e6a23c', percentage: 70 }, { color: '#EA4C4C', percentage: 100 }]" />
          </template>
        </el-table-column>
      </el-table>
      <div style="color: #999; font-size: 12px; margin-top: 8px">
        熱度＝20 日動能 30%＋5 日動能 15%＋站上月線 20%＋20 日新高 10%＋量比 10%＋法人 15%，各項先轉成同層級內的百分位（相對熱度）。
        成分股只計入交易滿 60 天、20 日均成交額 ≥500 萬的股票；等權平均容易被單一飆股拉高，搭配「站上月線」一起看。
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 12px">
          <b>輪動熱力圖</b>
          <el-radio-group v-model="metric" size="small" @change="loadHeat">
            <el-radio-button v-for="(m, k) in METRICS" :key="k" :value="k">{{ m.label }}</el-radio-button>
          </el-radio-group>
          <span style="color: #999; font-size: 12px">目前熱度前 {{ layer === 3 ? 20 : 25 }} 名 × 近 20 個交易日｜點格子看成分股</span>
        </div>
      </template>
      <div ref="heatEl" :style="{ width: '100%', height: heatHeight + 'px' }"></div>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px" v-loading="detailLoading">
      <template #header>
        <div v-if="detail" style="display: flex; align-items: baseline; flex-wrap: wrap; gap: 14px">
          <b style="font-size: 16px">{{ detail.name }}</b>
          <template v-if="detail.daily">
            <span>熱度第 <b>{{ detail.daily.heat_rank }}</b> 名（{{ Number(detail.daily.heat_score).toFixed(1) }}）</span>
            <span>20日 <b :style="{ color: up(detail.daily.ret_20d) }">{{ pctx(detail.daily.ret_20d) }}</b></span>
            <span>站上月線 <b>{{ Math.round(detail.daily.breadth_ma20 * 100) }}%</b></span>
            <span>法人 <b :style="{ color: up(detail.daily.inst_ratio) }">{{ pctx(detail.daily.inst_ratio) }}</b></span>
          </template>
          <span v-else style="color: #999">（成分股不足 5 檔，未計算熱度）</span>
          <span style="color: #999; font-size: 12px">共 {{ detail.rows.length }} 檔｜20 日報酬中位數 {{ pctx(detail.median_ret_20d) }}</span>
        </div>
        <span v-else style="color: #999">點上方族群看成分股</span>
      </template>
      <div v-if="detail?.note" style="color: #666; font-size: 13px; margin-bottom: 8px">{{ detail.note }}</div>
      <el-table v-if="detail" :data="detail.rows" stripe max-height="560" style="cursor: pointer"
                @row-click="(r) => go(r.stock_id)">
        <el-table-column label="" width="92">
          <template #default="{ row }">
            <el-tag v-if="row.tag" :type="TAG[row.tag].type" size="small" effect="plain">{{ TAG[row.tag].label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stock_id" label="代碼" width="72" />
        <el-table-column prop="name" label="名稱" width="110" show-overflow-tooltip />
        <el-table-column prop="industry" label="官方產業" width="110" show-overflow-tooltip />
        <el-table-column v-if="detail.layer === 3" label="角色／狀態" width="110">
          <template #default="{ row }">
            {{ row.role || '—' }}
            <span v-if="row.status === 'seed'" style="color: #e6a23c; font-size: 12px">（{{ STATUS[row.status] }}）</span>
          </template>
        </el-table-column>
        <el-table-column label="收盤" width="80" align="right">
          <template #default="{ row }">{{ row.close == null ? '—' : Number(row.close) }}</template>
        </el-table-column>
        <el-table-column label="1日" width="76" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.ret_1d) }">{{ pctx(row.ret_1d) }}</span></template>
        </el-table-column>
        <el-table-column label="5日" width="76" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.ret_5d) }">{{ pctx(row.ret_5d) }}</span></template>
        </el-table-column>
        <el-table-column label="20日" width="80" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.ret_20d) }">{{ pctx(row.ret_20d) }}</span></template>
        </el-table-column>
        <el-table-column label="月線／季線" width="92" align="center">
          <template #default="{ row }">
            <span :style="{ color: row.above_ma20 ? '#EA4C4C' : '#3F9E5A' }">{{ row.above_ma20 ? '上' : '下' }}</span>
            ／
            <span :style="{ color: row.above_ma60 ? '#EA4C4C' : '#3F9E5A' }">{{ row.above_ma60 ? '上' : '下' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="rs_rating" label="RS" width="56" align="right" />
        <el-table-column label="法人20日" width="100" align="right">
          <template #default="{ row }"><span :style="{ color: up(row.inst_net_20d) }">{{ lots(row.inst_net_20d) }}</span></template>
        </el-table-column>
        <el-table-column label="日均成交" width="88" align="right">
          <template #default="{ row }">
            <span :style="{ color: row.in_universe ? '' : '#bbb' }">{{ yi(row.amt20) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="為什麼在這個族群" min-width="200" show-overflow-tooltip>
          <template #default="{ row }"><span style="color: #888; font-size: 12px">{{ row.evidence }}</span></template>
        </el-table-column>
      </el-table>
      <div v-if="detail" style="color: #999; font-size: 12px; margin-top: 8px">
        領頭羊＝20 日報酬前 3 名；落後補漲＝20 日報酬低於族群中位數、但仍站上季線。灰色成交額＝未達母體門檻（不計入族群熱度）。點列進個股頁。
      </div>
    </el-card>
  </div>
</template>

<style scoped>
:deep(.current-theme) td { background: #fdf6ec !important; }
</style>
