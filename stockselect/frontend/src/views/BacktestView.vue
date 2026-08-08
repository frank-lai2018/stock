<script setup>
// 型態回測：顯示各型態突破後 N 日的勝率 / 平均報酬（由 backtest_patterns.py 離線算好）。
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getPatternBacktest } from '../api'

const data = ref({ items: [], horizons: [], computed_at: null })
const grp = ref('all')
const loading = ref(false)

const GROUP_NAME = { bottom: '底部反轉', top: '頭部反轉', continuation: '整理突破' }
const MIN_N = 30                       // 事件數低於此 → 樣本不足、統計不可信
const pct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%')
const wr = (v) => (v == null ? '—' : Number(v).toFixed(0) + '%')
// 方向調整後：正＝順預期方向獲利 → 紅；負 → 綠
const retClr = (v) => (v == null ? '' : Number(v) >= 0 ? '#EA4C4C' : '#3F9E5A')
const wrClr = (v) => (v == null ? '#909399' : v >= 55 ? '#EA4C4C' : v < 45 ? '#3F9E5A' : '#909399')

const items = computed(() =>
  grp.value === 'all' ? data.value.items : data.value.items.filter((x) => x.group === grp.value))

async function load() {
  loading.value = true
  try {
    data.value = await getPatternBacktest()
  } catch (e) {
    ElMessage.error('載入回測失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 12px">
      <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap">
        <b>型態回測</b>
        <el-radio-group v-model="grp" size="small">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="bottom">底部反轉</el-radio-button>
          <el-radio-button value="continuation">整理突破</el-radio-button>
          <el-radio-button value="top">頭部反轉</el-radio-button>
        </el-radio-group>
        <el-tag v-if="data.computed_at" type="info">回測時間 {{ data.computed_at.slice(0, 16).replace('T', ' ') }}</el-tag>
        <span style="color: #999; font-size: 12px">
          突破後 N 日報酬（方向調整：底部/多方漲為勝、頭部/空方跌為勝）；紅＝順預期方向獲利。
          <b>超額＝個股 − 同期加權指數</b>（扣掉大盤才是型態真本事）。事件數 &lt; {{ MIN_N }} 標灰＝樣本不足勿盡信。統計非保證。
        </span>
      </div>
    </el-card>

    <el-empty v-if="!loading && !items.length"
              description="尚無回測資料，請先在 stockselect/backend 執行：python backtest_patterns.py --limit 500" />

    <el-table v-else :data="items" v-loading="loading" stripe height="72vh">
      <el-table-column label="型態" width="150" fixed>
        <template #default="{ row }">
          {{ row.pattern_name }}
          <el-tag size="small" effect="plain">{{ GROUP_NAME[row.group] || row.group }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="事件數" width="94" align="right" sortable
                       :sort-method="(a, b) => (a.n ?? -1) - (b.n ?? -1)">
        <template #default="{ row }">
          <span :style="{ color: (row.n ?? 0) < MIN_N ? '#c0c4cc' : '#303133' }">{{ row.n ?? 0 }}</span>
          <el-tag v-if="(row.n ?? 0) < MIN_N" size="small" type="info" effect="plain" style="margin-left:4px">少</el-tag>
        </template>
      </el-table-column>
      <template v-for="h in data.horizons" :key="h">
        <el-table-column :label="`${h}日勝率`" width="90" align="right" sortable
                         :sort-method="(a, b) => (a.horizons[h]?.win_rate ?? -1) - (b.horizons[h]?.win_rate ?? -1)">
          <template #default="{ row }">
            <b :style="{ color: (row.n ?? 0) < MIN_N ? '#c0c4cc' : wrClr(row.horizons[h]?.win_rate) }">{{ wr(row.horizons[h]?.win_rate) }}</b>
          </template>
        </el-table-column>
        <el-table-column :label="`${h}日均報酬`" width="104" align="right" sortable
                         :sort-method="(a, b) => (a.horizons[h]?.avg_ret ?? -99) - (b.horizons[h]?.avg_ret ?? -99)">
          <template #default="{ row }"><span :style="{ color: (row.n ?? 0) < MIN_N ? '#c0c4cc' : retClr(row.horizons[h]?.avg_ret) }">{{ pct(row.horizons[h]?.avg_ret) }}</span></template>
        </el-table-column>
        <el-table-column :label="`${h}日超額`" width="100" align="right" sortable
                         :sort-method="(a, b) => (a.horizons[h]?.avg_excess ?? -99) - (b.horizons[h]?.avg_excess ?? -99)">
          <template #default="{ row }"><b :style="{ color: (row.n ?? 0) < MIN_N ? '#c0c4cc' : retClr(row.horizons[h]?.avg_excess) }">{{ pct(row.horizons[h]?.avg_excess) }}</b></template>
        </el-table-column>
      </template>
    </el-table>
  </div>
</template>
