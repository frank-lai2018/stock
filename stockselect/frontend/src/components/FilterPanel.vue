<script setup>
defineProps({
  filters: { type: Object, required: true },   // 直接雙向綁定其屬性（reactive 物件）
  strategies: { type: Object, default: () => ({}) },
  sort: { type: String, default: 'ret_3m' },
  limit: { type: Number, default: 50 },
})
const emit = defineEmits(['search', 'apply', 'update:sort', 'update:limit'])

const sortCols = [
  'ret_3m', 'ret_12_1', 'roe', 'per', 'dividend_yield',
  'rev_yoy', 'inst_net_20d', 'big1000_pct', 'amt20',
]
</script>

<template>
  <el-card shadow="never">
    <template #header><b>預設策略</b></template>
    <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 4px">
      <el-button v-for="(s, k) in strategies" :key="k" size="small"
                 @click="emit('apply', k)" :title="s.desc">{{ s.name }}</el-button>
    </div>

    <el-divider>條件</el-divider>
    <el-form label-width="96px" size="small">
      <el-form-item label="近3月報酬≥">
        <el-input-number v-model="filters.ret_3m_min" :step="0.05" :precision="2" controls-position="right" />
        <span style="margin-left:6px;color:#999">0.1=10%</span>
      </el-form-item>
      <el-form-item label="12-1動能≥">
        <el-input-number v-model="filters.ret_12_1_min" :step="0.05" :precision="2" controls-position="right" />
      </el-form-item>
      <el-form-item label="站上季線"><el-switch v-model="filters.above_ma60" /></el-form-item>
      <el-form-item label="ROE≥"><el-input-number v-model="filters.roe_min" controls-position="right" /></el-form-item>
      <el-form-item label="營收YoY≥"><el-input-number v-model="filters.rev_yoy_min" controls-position="right" /></el-form-item>
      <el-form-item label="負債比≤"><el-input-number v-model="filters.debt_ratio_max" controls-position="right" /></el-form-item>
      <el-form-item label="本益比≤"><el-input-number v-model="filters.per_max" controls-position="right" /></el-form-item>
      <el-form-item label="殖利率≥"><el-input-number v-model="filters.dividend_yield_min" :step="0.5" controls-position="right" /></el-form-item>
      <el-form-item label="法人20日≥">
        <el-input-number v-model="filters.inst_net_20d_min" :step="1000000" controls-position="right" />
        <span style="margin-left:6px;color:#999">股</span>
      </el-form-item>
      <el-form-item label="千張大戶%≥"><el-input-number v-model="filters.big1000_pct_min" controls-position="right" /></el-form-item>
      <el-form-item label="日均額≥">
        <el-input-number v-model="filters.amt20_min" :step="10000000" controls-position="right" />
        <span style="margin-left:6px;color:#999">元</span>
      </el-form-item>
      <el-form-item label="只看母體"><el-switch v-model="filters.in_universe" /></el-form-item>

      <el-divider>排序 / 筆數</el-divider>
      <el-form-item label="排序依">
        <el-select :model-value="sort" @update:model-value="(v) => emit('update:sort', v)" style="width: 160px">
          <el-option v-for="c in sortCols" :key="c" :label="c" :value="c" />
        </el-select>
      </el-form-item>
      <el-form-item label="回傳筆數">
        <el-input-number :model-value="limit" @update:model-value="(v) => emit('update:limit', v)"
                         :min="1" :max="500" controls-position="right" />
      </el-form-item>
      <el-button type="primary" style="width: 100%" @click="emit('search')">🔍 篩選</el-button>
    </el-form>
  </el-card>
</template>
