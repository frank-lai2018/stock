<script setup>
import { useRouter } from 'vue-router'

defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})
const router = useRouter()

const pct = (v) => (v == null ? '' : (Number(v) * 100).toFixed(1) + '%')
const pct1 = (v) => (v == null ? '' : Number(v).toFixed(2) + '%')
const num = (v) => (v == null ? '' : Number(v).toLocaleString('en-US'))
const cmp = (k) => (a, b) => (a[k] ?? -Infinity) - (b[k] ?? -Infinity)
const dirColor = { bull: '#EA4C4C', bear: '#3F9E5A', neutral: '#909399' }   // 紅多綠空

function go(row) {
  router.push(`/stock/${row.stock_id}`)
}
</script>

<template>
  <el-table :data="items" v-loading="loading" height="74vh" stripe @row-click="go"
            style="cursor: pointer" :default-sort="{ prop: '', order: '' }">
    <el-table-column prop="stock_id" label="代碼" width="80" fixed />
    <el-table-column prop="name" label="名稱" width="110" fixed />
    <el-table-column prop="industry" label="產業" width="120" show-overflow-tooltip />
    <el-table-column label="RS評等" width="90" :sort-method="cmp('rs_rating')" sortable>
      <template #default="{ row }">
        <b v-if="row.rs_rating != null"
           :style="{ color: row.rs_rating >= 70 ? '#f56c6c' : '#909399' }">{{ row.rs_rating }}</b>
      </template>
    </el-table-column>
    <el-table-column label="K棒型態" width="130">
      <template #default="{ row }">
        <el-tag v-for="(p, i) in (row.last_patterns || [])" :key="i"
                :color="dirColor[p.dir]" size="small"
                style="color: #fff; border: 0; margin: 1px 2px">{{ p.name }}</el-tag>
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
  </el-table>
</template>
