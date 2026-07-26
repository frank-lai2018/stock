<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import WatchlistAddButton from './WatchlistAddButton.vue'
import WatchlistBatchAdd from './WatchlistBatchAdd.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectable: { type: Boolean, default: false },   // 開勾選欄 + 批次加入自選股
})
const router = useRouter()
const tableRef = ref()
const selected = ref([])
const hasBreakout = computed(() => (props.items || []).some((r) => r.breakout))
function onSel(rows) { selected.value = rows }
function clearSel() { tableRef.value?.clearSelection(); selected.value = [] }

const pct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
const pct1 = (v) => (v == null ? '' : Number(v).toFixed(2) + '%')
const num = (v) => (v == null ? '' : Number(v).toLocaleString('en-US'))
const cmp = (k) => (a, b) => (a[k] ?? -Infinity) - (b[k] ?? -Infinity)
const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }   // 紅多綠空

function go(row, column) {
  if (column && column.type === 'selection') return   // 點勾選格不跳頁
  router.push(`/stock/${row.stock_id}`)
}
</script>

<template>
  <div v-if="selectable && selected.length"
       style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px">
    <span>已勾選 <b style="color: #EA4C4C">{{ selected.length }}</b> 檔</span>
    <WatchlistBatchAdd :rows="selected" @done="clearSel" />
    <el-button size="small" text @click="clearSel">清除勾選</el-button>
  </div>
  <el-table ref="tableRef" :data="items" v-loading="loading" height="74vh" stripe @row-click="go"
            @selection-change="onSel" style="cursor: pointer" :default-sort="{ prop: '', order: '' }">
    <el-table-column v-if="selectable" type="selection" width="42" fixed />
    <el-table-column prop="stock_id" label="代碼" width="80" fixed />
    <el-table-column label="名稱" width="130" fixed>
      <template #default="{ row }">
        {{ row.name }}
        <el-tag v-if="row.security_type === 'etf'" size="small" type="warning" effect="plain">ETF</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="industry" label="產業" width="120" show-overflow-tooltip />
    <el-table-column label="RS評等" width="90" :sort-method="cmp('rs_rating')" sortable>
      <template #default="{ row }">
        <b v-if="row.rs_rating != null"
           :style="{ color: row.rs_rating >= 70 ? '#f56c6c' : '#909399' }">{{ row.rs_rating }}</b>
      </template>
    </el-table-column>
    <el-table-column label="股價" width="88" :sort-method="cmp('close')" sortable>
      <template #default="{ row }">{{ row.close ?? '—' }}</template>
    </el-table-column>
    <el-table-column label="K棒型態" width="130">
      <template #default="{ row }">
        <el-tag v-for="(p, i) in (row.last_patterns || [])" :key="i"
                :color="dirColor[p.dir]" size="small"
                style="color: #fff; border: 0; margin: 1px 2px">{{ p.name }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column v-if="hasBreakout" label="W底突破" width="190">
      <template #default="{ row }">
        <template v-if="row.breakout">
          <div style="font-size: 12px; line-height: 1.5">
            <span style="color: #EA4C4C">突破 {{ row.breakout.breakout_date?.slice(5) }}</span>
            ｜頸線 {{ row.breakout.neckline }}
            <br />
            滿足價 <b style="color: #EA4C4C">{{ row.breakout.target }}</b>
            ｜量 {{ row.breakout.vol_ratio }}×
          </div>
        </template>
      </template>
    </el-table-column>
    <el-table-column label="近3月" width="90" :sort-method="cmp('ret_3m')" sortable>
      <template #default="{ row }">
        <span :style="{ color: row.ret_3m >= 0 ? '#f56c6c' : '#67c23a' }">{{ pct(row.ret_3m) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="12-1動能" width="100" :sort-method="cmp('ret_12_1')" sortable>
      <template #default="{ row }">{{ pct(row.ret_12_1) }}</template>
    </el-table-column>
    <el-table-column label="ROE" width="80" :sort-method="cmp('roe')" sortable>
      <template #default="{ row }">{{ row.roe }}</template>
    </el-table-column>
    <el-table-column label="PER" width="80" :sort-method="cmp('per')" sortable>
      <template #default="{ row }">{{ row.per }}</template>
    </el-table-column>
    <el-table-column label="殖利率" width="90" :sort-method="cmp('dividend_yield')" sortable>
      <template #default="{ row }">{{ pct1(row.dividend_yield) }}</template>
    </el-table-column>
    <el-table-column label="營收YoY" width="100" :sort-method="cmp('rev_yoy')" sortable>
      <template #default="{ row }">{{ pct1(row.rev_yoy) }}</template>
    </el-table-column>
    <el-table-column label="法人20日(股)" width="130" :sort-method="cmp('inst_net_20d')" sortable>
      <template #default="{ row }">
        <span :style="{ color: row.inst_net_20d >= 0 ? '#f56c6c' : '#67c23a' }">{{ num(row.inst_net_20d) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="千張大戶%" width="100" :sort-method="cmp('big1000_pct')" sortable>
      <template #default="{ row }">{{ row.big1000_pct }}</template>
    </el-table-column>
    <el-table-column label="承接/出貨" width="100" :sort-method="cmp('vpa_accum_20d')" sortable>
      <template #default="{ row }">
        <span style="color: #f56c6c">{{ row.vpa_accum_20d ?? 0 }}</span>
        <span style="color: #999"> / </span>
        <span style="color: #67c23a">{{ row.vpa_distrib_20d ?? 0 }}</span>
      </template>
    </el-table-column>
    <el-table-column label="站季線" width="80">
      <template #default="{ row }">
        <el-tag v-if="row.above_ma60" type="success" size="small">是</el-tag>
        <el-tag v-else type="info" size="small">否</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="自選" width="66" fixed="right">
      <template #default="{ row }"><WatchlistAddButton :row="row" /></template>
    </el-table-column>
  </el-table>
</template>
