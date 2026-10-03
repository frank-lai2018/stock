<script setup>
// 美台題材對照：美股最新一個交易日（台灣隔天清晨收盤）各題材籃子的漲跌與台股反應、20 日強弱四象限
// 資料由 fetch_us_prices.py／build_us_theme_daily.py 每晚產生（nightly 的 usprice／ustheme），
// 跟隨度由 analyze_us_tw_themes.py 寫入；說明見 美台題材對照.md
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getUsThemeCompare } from '../api'

const router = useRouter()
const data = ref({ themes: [], indicators: [], medians: {} })
const loading = ref(false)
const overnightTable = ref(null)
const quadrantTable = ref(null)

const pctx = (v, d = 1) => (v == null ? '—' : (v >= 0 ? '+' : '') + (Number(v) * 100).toFixed(d) + '%')
const up = (v) => (v == null ? '#999' : v >= 0 ? '#EA4C4C' : '#3F9E5A')
const FOLLOW_TYPE = { 明顯跟隨: 'danger', 有一點: 'warning', 幾乎不跟: 'info' }
const QUADRANT = {
  美強台強: { type: 'danger', note: '全球主流' },
  美強台弱: { type: 'warning', note: '台股還沒跟上' },
  美弱台強: { type: 'primary', note: '台股自己的題材' },
  美弱台弱: { type: 'info', note: '冷門' },
  美股無對應: { type: 'info', note: '美股沒有同業' },
}
const ORDER = Object.keys(QUADRANT)

// 昨晚美股強勢族群：依美股籃子昨晚漲幅扣 SPY 排序（沒有美股籃子的題材不列）
const overnight = computed(() => (data.value.themes || []).filter((t) => t.us)
  .sort((a, b) => (b.us.ex_1d ?? -9) - (a.us.ex_1d ?? -9)))
// 20 日四象限：象限順序 → 美股 20 日漲幅
const quadrants = computed(() => (data.value.themes || []).filter((t) => t.quadrant)
  .sort((a, b) => ORDER.indexOf(a.quadrant) - ORDER.indexOf(b.quadrant) || (b.us?.ret_20d ?? -9) - (a.us?.ret_20d ?? -9)))
const period = computed(() => {
  const m = data.value.follow_meta
  return m ? `${m.period_from}～${m.period_to}，${m.n_days} 個交易日` : '尚未計算'
})

const leaders = (t, key, n = 2) => [...(t.members || [])].filter((m) => m[key] != null)
  .sort((a, b) => b[key] - a[key]).slice(0, n)
const followTip = (f) => `扣掉兩邊大盤後的隔天相關 ${f.corr_ex?.toFixed(2)}｜美股籃子大漲（前 10%）隔天，台股平均超額 ` +
  `${pctx(f.up_next, 2)}、${Math.round((f.up_win ?? 0) * 100)}% 跑贏大盤｜大跌（後 10%）隔天 ${pctx(f.down_next, 2)}` +
  (f.sox_only ? '｜扣掉費半後這籃美股沒有多出資訊，看費半就夠' : '')

async function load() {
  loading.value = true
  try {
    data.value = await getUsThemeCompare()
  } catch (e) {
    ElMessage.error('載入美台題材對照失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

const toggle = (table, row) => table.value?.toggleRowExpansion(row)
const goTheme = (code) => router.push({ path: '/themes', query: { code, layer: 3 } })

onMounted(load)
</script>

<template>
  <div class="us-themes">
    <el-card shadow="never" class="block">
      <div class="title-row">
        <div>
          <h2>美台題材對照</h2>
          <div class="muted">
            美股 {{ data.us_date || '—' }}（台灣 {{ data.us_close_tw || '—' }} 清晨收盤）｜台股 {{ data.tw_date || '—' }}｜每晚排程更新
          </div>
        </div>
        <el-button type="primary" :loading="loading" @click="load">重新整理</el-button>
      </div>
      <div class="indicators">
        <div v-for="i in data.indicators" :key="i.symbol" class="indicator">
          <span>{{ i.name }}</span>
          <b :style="{ color: up(i.ret_1d) }">{{ pctx(i.ret_1d) }}</b>
          <small>20 日 <span :style="{ color: up(i.ret_20d) }">{{ pctx(i.ret_20d) }}</span></small>
        </div>
      </div>
    </el-card>

    <el-empty v-if="!loading && !data.us_date" description="還沒有美股資料：先跑 fetch_us_prices.py --range 5y 與 build_us_theme_daily.py" />

    <template v-else>
      <el-alert type="info" :closable="false" show-icon class="block">
        <template #title>美股收盤是台灣隔天清晨：晚上看到的「昨晚美股」，已經反映在台股當天的交易</template>
        「台股反應」是美股這一場之後第一個台股交易日的題材漲跌，可以逐日觀察有沒有跟；週末或連假還沒開盤時顯示「待開盤」。
        跟隨度依近兩年回測（{{ period }}），滑鼠移到標籤上看細節。
      </el-alert>

      <el-card shadow="never" class="block">
        <template #header>
          <div class="card-head">
            <b>昨晚美股強勢族群 → 台股{{ data.react_date ? ` ${data.react_date} ` : '下個交易日' }}反應</b>
            <span class="muted">
              依美股籃子昨晚漲幅扣 SPY 排序｜點列看美股成分
              <template v-if="data.react_market">｜台股當天：加權 {{ pctx(data.react_market.twse) }}、題材中位數 {{ pctx(data.react_market.theme_median) }}</template>
            </span>
          </div>
        </template>
        <el-table ref="overnightTable" :data="overnight" row-key="code" stripe size="small" style="cursor: pointer"
                  @row-click="(row) => toggle(overnightTable, row)">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="members">
                <el-table :data="row.members" size="small" border>
                  <el-table-column prop="symbol" label="代號" width="80" />
                  <el-table-column prop="name" label="名稱" min-width="160" />
                  <el-table-column label="昨晚" width="85" align="right">
                    <template #default="{ row: m }"><span :style="{ color: up(m.ret_1d) }">{{ pctx(m.ret_1d) }}</span></template>
                  </el-table-column>
                  <el-table-column label="5 日" width="85" align="right">
                    <template #default="{ row: m }"><span :style="{ color: up(m.ret_5d) }">{{ pctx(m.ret_5d) }}</span></template>
                  </el-table-column>
                  <el-table-column label="20 日" width="85" align="right">
                    <template #default="{ row: m }"><span :style="{ color: up(m.ret_20d) }">{{ pctx(m.ret_20d) }}</span></template>
                  </el-table-column>
                </el-table>
                <div v-if="row.follow" class="muted small follow-line">{{ followTip(row.follow) }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="題材" min-width="210">
            <template #default="{ row }"><el-link type="primary" @click.stop="goTheme(row.code)">{{ row.name }}</el-link></template>
          </el-table-column>
          <el-table-column label="美股昨晚" width="95" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.us.ret_1d) }">{{ pctx(row.us.ret_1d) }}</span></template>
          </el-table-column>
          <el-table-column label="扣 SPY" width="90" align="right">
            <template #default="{ row }"><b :style="{ color: up(row.us.ex_1d) }">{{ pctx(row.us.ex_1d) }}</b></template>
          </el-table-column>
          <el-table-column label="昨晚領漲" min-width="220">
            <template #default="{ row }">
              <span v-for="m in leaders(row, 'ret_1d')" :key="m.symbol" class="leader">
                {{ m.name }} <span :style="{ color: up(m.ret_1d) }">{{ pctx(m.ret_1d) }}</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="隔天跟隨度" width="180">
            <template #default="{ row }">
              <template v-if="row.follow">
                <el-tooltip :content="followTip(row.follow)" placement="top">
                  <el-tag size="small" :type="FOLLOW_TYPE[row.follow.label]" effect="plain">{{ row.follow.label }}</el-tag>
                </el-tooltip>
                <el-tag v-if="row.follow.sox_only" size="small" type="info" effect="plain" class="gap">看費半就夠</el-tag>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="台股反應" width="95" align="right">
            <template #default="{ row }">
              <b v-if="row.react_ret != null" :style="{ color: up(row.react_ret) }">{{ pctx(row.react_ret) }}</b>
              <span v-else class="muted">待開盤</span>
            </template>
          </el-table-column>
          <el-table-column label="台股熱度名次" width="105" align="center">
            <template #default="{ row }">{{ row.tw?.rank ?? '—' }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never" class="block">
        <template #header>
          <div class="card-head">
            <b>20 日強弱四象限</b>
            <span class="muted">
              兩邊各以 20 日漲幅高於同日題材中位數＝強（台股中位 {{ pctx(data.medians?.tw_20d) }}、美股中位 {{ pctx(data.medians?.us_20d) }}）；
              回測美股強的題材，之後 20 天台股同題材平均多漲 1.2～1.6%
            </span>
          </div>
        </template>
        <el-table ref="quadrantTable" :data="quadrants" row-key="code" stripe size="small" style="cursor: pointer"
                  @row-click="(row) => toggle(quadrantTable, row)">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="members">
                <el-table v-if="row.members.length" :data="row.members" size="small" border>
                  <el-table-column prop="symbol" label="代號" width="80" />
                  <el-table-column prop="name" label="名稱" min-width="160" />
                  <el-table-column label="昨晚" width="85" align="right">
                    <template #default="{ row: m }"><span :style="{ color: up(m.ret_1d) }">{{ pctx(m.ret_1d) }}</span></template>
                  </el-table-column>
                  <el-table-column label="5 日" width="85" align="right">
                    <template #default="{ row: m }"><span :style="{ color: up(m.ret_5d) }">{{ pctx(m.ret_5d) }}</span></template>
                  </el-table-column>
                  <el-table-column label="20 日" width="85" align="right">
                    <template #default="{ row: m }"><span :style="{ color: up(m.ret_20d) }">{{ pctx(m.ret_20d) }}</span></template>
                  </el-table-column>
                </el-table>
                <span v-else class="muted">美股沒有對應的同業</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="象限" width="170">
            <template #default="{ row }">
              <el-tag size="small" :type="QUADRANT[row.quadrant].type" effect="dark">{{ row.quadrant }}</el-tag>
              <span class="muted small gap">{{ QUADRANT[row.quadrant].note }}</span>
            </template>
          </el-table-column>
          <el-table-column label="題材" min-width="200">
            <template #default="{ row }"><el-link type="primary" @click.stop="goTheme(row.code)">{{ row.name }}</el-link></template>
          </el-table-column>
          <el-table-column label="台股 20 日" width="90" align="right">
            <template #default="{ row }"><b :style="{ color: up(row.tw?.ret_20d) }">{{ pctx(row.tw?.ret_20d) }}</b></template>
          </el-table-column>
          <el-table-column label="台股 5 日" width="85" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.tw?.ret_5d) }">{{ pctx(row.tw?.ret_5d) }}</span></template>
          </el-table-column>
          <el-table-column label="台股名次" width="80" align="center">
            <template #default="{ row }">{{ row.tw?.rank ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="美股 20 日" width="90" align="right">
            <template #default="{ row }"><b :style="{ color: up(row.us?.ret_20d) }">{{ pctx(row.us?.ret_20d) }}</b></template>
          </el-table-column>
          <el-table-column label="扣 SPY" width="85" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.us?.ex_20d) }">{{ pctx(row.us?.ex_20d) }}</span></template>
          </el-table-column>
          <el-table-column label="美股 5 日" width="85" align="right">
            <template #default="{ row }"><span :style="{ color: up(row.us?.ret_5d) }">{{ pctx(row.us?.ret_5d) }}</span></template>
          </el-table-column>
          <el-table-column label="美股名次" width="80" align="center">
            <template #default="{ row }">{{ row.us?.rank ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="美股領漲（20 日）" min-width="220">
            <template #default="{ row }">
              <span v-for="m in leaders(row, 'ret_20d')" :key="m.symbol" class="leader">
                {{ m.name }} <span :style="{ color: up(m.ret_20d) }">{{ pctx(m.ret_20d, 0) }}</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="隔天跟隨度" width="180">
            <template #default="{ row }">
              <template v-if="row.follow">
                <el-tooltip :content="followTip(row.follow)" placement="top">
                  <el-tag size="small" :type="FOLLOW_TYPE[row.follow.label]" effect="plain">{{ row.follow.label }}</el-tag>
                </el-tooltip>
                <el-tag v-if="row.follow.sox_only" size="small" type="info" effect="plain" class="gap">看費半就夠</el-tag>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.us-themes { max-width: 1700px; margin: 0 auto; }
.block { margin-bottom: 12px; }
.title-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }
h2 { margin: 0 0 4px; font-size: 22px; }
.muted { color: #909399; }
.small { font-size: 12px; }
.gap { margin-left: 6px; }
.indicators { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-top: 14px; }
.indicator { display: flex; align-items: baseline; gap: 8px; padding: 10px 14px; border: 1px solid #ebeef5; border-radius: 6px; }
.indicator b { font-size: 20px; }
.indicator small { color: #909399; margin-left: auto; }
.card-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.card-head .muted { font-size: 12px; }
.members { padding: 4px 48px 8px; max-width: 640px; }
.follow-line { margin-top: 6px; }
.leader { margin-right: 12px; white-space: nowrap; }
@media (max-width: 900px) { .indicators { grid-template-columns: repeat(2, 1fr); } }
</style>
