<script setup>
// 型態突破結果表（共用）：型態突破/頭部/整理三頁與自選股分類頁共用同一張表，確保欄位一致。
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import WatchlistBatchAdd from './WatchlistBatchAdd.vue'

defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  showTarget: { type: Boolean, default: true },   // 顯示「量測滿足價 / 方向」兩欄
  showDir: { type: Boolean, default: false },     // 連續/頭部型有多空方向
  selectable: { type: Boolean, default: false },  // 開勾選欄 + 批次加入自選股
  near: { type: Boolean, default: false },        // 接近突破模式：顯示「距突破%」、突破日改標「最新」
  showHold: { type: Boolean, default: false },    // 自選股：顯示進場價 / 進場日 / 持有報酬
  showTrack: { type: Boolean, default: false },   // 自選股：突破後實際 vs 型態回測期望對照
})
const nearPct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
// 「相對型態」提示：實際(順型態方向) − 該型態回測同期均報酬
function trackTip(row) {
  const t = row.track
  if (!t) return ''
  const p = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%')
  return `突破後 ${t.days} 個交易日${t.over ? '（已過觀察期，以最長持有期比對）' : ''}\n`
    + `實際(順勢) ${p(t.actual)}　型態${t.horizon}日均 ${p(t.exp_ret)}`
    + (t.win_rate != null ? `（勝率${t.win_rate.toFixed(0)}%）` : '')
}

const router = useRouter()
const tableRef = ref()
const selected = ref([])
const pct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
const upc = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
// 方向上色（紅漲綠跌）；底部型態一律偏多
const dirColor = (row) => (row.breakout?.dir === 'bear' ? '#3F9E5A' : '#EA4C4C')
const kbarColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }   // 最新K棒型態
function go(row, column) {
  if (column && column.type === 'selection') return   // 點勾選格不跳頁
  router.push(`/stock/${row.stock_id}`)
}
function pts(row) {
  return (row.breakout?.points || []).map((p) => `${p.label} ${p.date.slice(5)} @${p.price}`).join('　')
}
function onSel(rows) { selected.value = rows }
function clearSel() { tableRef.value?.clearSelection(); selected.value = [] }
</script>

<template>
  <div v-if="selectable && selected.length"
       style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
    <span>已勾選 <b style="color: #EA4C4C">{{ selected.length }}</b> 檔</span>
    <WatchlistBatchAdd :rows="selected" @done="clearSel" />
    <el-button size="small" text @click="clearSel">清除勾選</el-button>
  </div>
  <el-table ref="tableRef" :data="items" v-loading="loading" stripe
            style="cursor: pointer" @row-click="go" @selection-change="onSel">
    <el-table-column v-if="selectable" type="selection" width="42" fixed />
    <el-table-column prop="stock_id" label="代碼" width="76" fixed />
    <el-table-column label="名稱" width="120" fixed>
      <template #default="{ row }">
        {{ row.name }}
        <el-tag v-if="row.security_type === 'etf'" size="small" type="warning" effect="plain">ETF</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="K棒型態" width="130">
      <template #default="{ row }">
        <el-tag v-for="(p, i) in (row.last_patterns || [])" :key="i"
                :color="kbarColor[p.dir]" size="small"
                style="color: #fff; border: 0; margin: 1px 2px">{{ p.name }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="型態" width="120">
      <template #default="{ row }">
        <el-tooltip :content="pts(row)" placement="top" :disabled="!pts(row)">
          <el-tag v-if="row.pattern_name" :color="dirColor(row)" style="color: #fff; border: 0">{{ row.pattern_name }}</el-tag>
          <span v-else style="color: #bbb">—</span>
        </el-tooltip>
      </template>
    </el-table-column>
    <el-table-column v-if="showTarget" label="量測滿足價" width="102">
      <template #default="{ row }"><b :style="{ color: dirColor(row) }">{{ row.breakout?.target }}</b></template>
    </el-table-column>
    <el-table-column v-if="showTarget && showDir" label="方向" width="72">
      <template #default="{ row }">
        <span v-if="row.breakout" :style="{ color: dirColor(row), fontWeight: 700 }">
          {{ row.breakout?.dir === 'bear' ? '空 ↓' : '多 ↑' }}
        </span>
      </template>
    </el-table-column>
    <el-table-column v-if="near" label="距突破" width="90" sortable
                     :sort-method="(a, b) => (a.breakout?.near_pct ?? 9) - (b.breakout?.near_pct ?? 9)">
      <template #default="{ row }"><b :style="{ color: dirColor(row) }">{{ nearPct(row.breakout?.near_pct) }}</b></template>
    </el-table-column>
    <el-table-column :label="near ? '最新' : '突破日'" width="104">
      <template #default="{ row }"><span :style="{ color: dirColor(row) }">{{ row.breakout?.breakout_date?.slice(5) }}</span></template>
    </el-table-column>
    <el-table-column v-if="!near" label="突破後" width="88" sortable
                     :sort-method="(a, b) => (a.breakout?.since_pct ?? -9) - (b.breakout?.since_pct ?? -9)">
      <template #default="{ row }"><span :style="{ color: upc(row.breakout?.since_pct) }">{{ nearPct(row.breakout?.since_pct) }}</span></template>
    </el-table-column>
    <el-table-column v-if="showTrack" label="型態同期均" width="104">
      <template #default="{ row }">
        <el-tooltip :content="trackTip(row)" placement="top" :disabled="!row.track">
          <span v-if="row.track">
            <span :style="{ color: upc(row.track.exp_ret) }">{{ nearPct(row.track.exp_ret) }}</span>
            <div style="color: #bbb; font-size: 11px; line-height: 1">
              突破後{{ row.track.days }}日{{ row.track.over ? '↑' : '' }}</div>
          </span>
          <span v-else style="color: #ccc">—</span>
        </el-tooltip>
      </template>
    </el-table-column>
    <el-table-column v-if="showTrack" label="相對型態" width="100" sortable
                     :sort-method="(a, b) => (a.track?.rel ?? -9) - (b.track?.rel ?? -9)">
      <template #default="{ row }">
        <b v-if="row.track?.rel != null" :style="{ color: upc(row.track.rel) }">
          {{ row.track.rel >= 0 ? '▲優 ' : '▼弱 ' }}{{ nearPct(row.track.rel) }}</b>
        <span v-else style="color: #ccc">—</span>
      </template>
    </el-table-column>
    <el-table-column :label="showDir ? '突破線' : '頸線/杯口'" width="96">
      <template #default="{ row }">{{ row.breakout?.neckline }}</template>
    </el-table-column>
    <el-table-column label="突破收盤" width="90">
      <template #default="{ row }">{{ row.breakout?.breakout_close }}</template>
    </el-table-column>
    <el-table-column label="量比" width="72">
      <template #default="{ row }">{{ row.breakout?.vol_ratio != null ? row.breakout.vol_ratio + '×' : '' }}</template>
    </el-table-column>
    <el-table-column label="RS評等" width="82" sortable :sort-method="(a, b) => (a.rs_rating ?? -1) - (b.rs_rating ?? -1)">
      <template #default="{ row }">
        <b :style="{ color: row.rs_rating >= 70 ? '#f56c6c' : '#909399' }">{{ row.rs_rating ?? '—' }}</b>
      </template>
    </el-table-column>
    <el-table-column label="股價" width="78">
      <template #default="{ row }">{{ row.close ?? '—' }}</template>
    </el-table-column>
    <el-table-column v-if="showHold" label="進場價" width="82">
      <template #default="{ row }">
        <span>{{ row.entry_price ?? '—' }}</span>
        <div v-if="row.entry_date" style="color: #bbb; font-size: 11px; line-height: 1">{{ row.entry_date.slice(5) }}</div>
      </template>
    </el-table-column>
    <el-table-column v-if="showHold" label="持有報酬" width="92" sortable
                     :sort-method="(a, b) => (a.hold_pct ?? -9) - (b.hold_pct ?? -9)">
      <template #default="{ row }"><b :style="{ color: upc(row.hold_pct) }">{{ pct(row.hold_pct) }}</b></template>
    </el-table-column>
    <el-table-column label="近3月" width="86" sortable :sort-method="(a, b) => (a.ret_3m ?? -9) - (b.ret_3m ?? -9)">
      <template #default="{ row }"><span :style="{ color: upc(row.ret_3m) }">{{ pct(row.ret_3m) }}</span></template>
    </el-table-column>
    <el-table-column prop="industry" label="產業" min-width="120" show-overflow-tooltip />
    <el-table-column v-if="$slots.action" label="操作" width="66" fixed="right">
      <template #default="{ row }"><slot name="action" :row="row" /></template>
    </el-table-column>
  </el-table>
</template>
