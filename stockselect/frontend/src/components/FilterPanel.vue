<script setup>
import { ref, onMounted } from 'vue'
import { getIndustries, getThemeOptions } from '../api'

defineProps({
  filters: { type: Object, required: true },   // 直接雙向綁定其屬性（reactive 物件）
  strategies: { type: Object, default: () => ({}) },
  sort: { type: String, default: 'ret_3m' },
  limit: { type: Number, default: 50 },
})
const emit = defineEmits(['search', 'apply', 'update:sort', 'update:limit'])

const sortCols = [
  'ret_3m', 'ret_12_1', 'rs_rating', 'roe', 'per', 'per_pctile', 'dividend_yield',
  'rev_yoy', 'eps_yoy', 'eps_qoq', 'eps_ttm', 'gross_margin', 'gross_margin_chg', 'op_margin', 'op_margin_chg',
  'inst_net_20d', 'big1000_pct', 'big1000_chg', 'big1000_up_weeks', 'retail_chg',
  'margin_util', 'short_margin_ratio', 'amt20',
  'vpa_accum_20d', 'vpa_distrib_20d',
]

const industries = ref([])
const themes3 = ref([])                  // 市場題材（L3）
const themes2 = ref([])                  // 櫃買產業鏈節點（L2）
onMounted(async () => {
  try { industries.value = await getIndustries() } catch (e) { /* 下拉沒清單不影響篩選 */ }
  try {
    const t = await getThemeOptions()
    themes3.value = t.filter((x) => x.layer === 3)
    themes2.value = t.filter((x) => x.layer === 2)
  } catch (e) { /* 族群表尚未建立時不影響其他篩選 */ }
})
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
      <el-form-item label="證券類別">
        <el-select v-model="filters.security_type" style="width: 160px">
          <el-option label="全部（含 ETF）" value="" />
          <el-option label="只看個股" value="stock" />
          <el-option label="只看 ETF" value="etf" />
        </el-select>
      </el-form-item>
      <el-form-item label="產業">
        <el-select v-model="filters.industry" style="width: 160px" filterable clearable placeholder="全部產業">
          <el-option v-for="n in industries" :key="n" :label="n" :value="n" />
        </el-select>
      </el-form-item>
      <el-form-item label="族群">
        <el-select v-model="filters.theme" style="width: 160px" filterable clearable placeholder="全部族群">
          <el-option-group label="市場題材">
            <el-option v-for="t in themes3" :key="t.code" :label="`${t.name}（${t.n}）`" :value="t.code" />
          </el-option-group>
          <el-option-group label="產業鏈節點（櫃買）">
            <el-option v-for="t in themes2" :key="t.code" :label="`${t.name}（${t.n}）`" :value="t.code" />
          </el-option-group>
        </el-select>
      </el-form-item>
      <!-- 主動式 ETF：近 5 個持股日有 N 家投信主動新建倉／加碼（或出清／減碼），已扣申購贖回；見 主動ETF追蹤設計.md -->
      <el-form-item label="主動ETF加碼">
        <el-select v-model="filters.aetf_buy_5d_min" style="width: 160px" clearable placeholder="不限">
          <el-option v-for="n in [1, 2, 3]" :key="n" :label="`近5日 ≥${n} 家投信`" :value="n" />
        </el-select>
      </el-form-item>
      <el-form-item label="主動ETF減碼">
        <el-select v-model="filters.aetf_sell_5d_min" style="width: 160px" clearable placeholder="不限">
          <el-option v-for="n in [1, 2, 3]" :key="n" :label="`近5日 ≥${n} 家投信`" :value="n" />
        </el-select>
      </el-form-item>
      <!-- 回測：主動 ETF 持股籃本身贏大盤最明顯（比跟著每日加碼有效）；見「主動ETF」頁的訊號回測 -->
      <el-form-item label="主動ETF持有">
        <el-select v-model="filters.aetf_held_min" style="width: 160px" clearable placeholder="不限">
          <el-option v-for="n in [1, 2, 3]" :key="n" :label="`目前 ≥${n} 家投信持有`" :value="n" />
        </el-select>
      </el-form-item>
      <el-form-item label="近3月報酬≥">
        <el-input-number v-model="filters.ret_3m_min" :step="0.05" :precision="2" controls-position="right" />
        <span style="margin-left:6px;color:#999">0.1=10%</span>
      </el-form-item>
      <el-form-item label="12-1動能≥">
        <el-input-number v-model="filters.ret_12_1_min" :step="0.05" :precision="2" controls-position="right" />
      </el-form-item>
      <el-form-item label="站上季線"><el-switch v-model="filters.above_ma60" /></el-form-item>
      <el-form-item label="RS評等≥"><el-input-number v-model="filters.rs_rating_min" :min="0" :max="99" controls-position="right" /></el-form-item>
      <el-form-item label="距52週高≥">
        <el-input-number v-model="filters.dist_52w_high_min" :step="0.05" :precision="2" controls-position="right" />
        <span style="margin-left:6px;color:#999">-0.25=25%內</span>
      </el-form-item>
      <el-form-item label="趨勢範本"><el-switch v-model="filters.trend_template" /></el-form-item>
      <el-form-item label="VCP 收縮"><el-switch v-model="filters.vcp" /></el-form-item>
      <el-form-item label="主力承接"><el-switch v-model="filters.mf_accumulate" /></el-form-item>
      <el-form-item label="主力出貨"><el-switch v-model="filters.mf_distribute" /></el-form-item>

      <el-divider>磚形圖 / 三線反轉（降噪過濾層）</el-divider>
      <el-form-item label="磚形圖為多">
        <el-switch v-model="filters.renko_bull" />
        <span style="margin-left:6px;color:#999">Renko 目前紅磚</span>
      </el-form-item>
      <el-form-item label="連續同向≥">
        <el-input-number v-model="filters.renko_run_min" :min="1" :max="20" controls-position="right" />
        <span style="margin-left:6px;color:#999">塊，越多動能越延續</span>
      </el-form-item>
      <el-form-item label="剛翻多">
        <el-switch v-model="filters.renko_fresh_bull" />
        <span style="margin-left:6px;color:#999">10 個交易日內由空翻多</span>
      </el-form-item>
      <el-form-item label="翻轉在">
        <el-input-number v-model="filters.renko_flip_days_max" :min="1" :max="120" controls-position="right" />
        <span style="margin-left:6px;color:#999">個交易日內</span>
      </el-form-item>
      <el-form-item label="三線同向">
        <el-switch v-model="filters.renko_tlb_agree" />
        <span style="margin-left:6px;color:#999">兩張圖方向一致＝第二確認</span>
      </el-form-item>
      <el-form-item label="ROE≥"><el-input-number v-model="filters.roe_min" controls-position="right" /></el-form-item>
      <el-form-item label="營收YoY≥"><el-input-number v-model="filters.rev_yoy_min" controls-position="right" /></el-form-item>
      <el-form-item label="負債比≤"><el-input-number v-model="filters.debt_ratio_max" controls-position="right" /></el-form-item>

      <el-divider>財報成長</el-divider>
      <el-form-item label="EPS年增≥">
        <el-input-number v-model="filters.eps_yoy_min" :step="10" controls-position="right" />
        <span style="margin-left:6px;color:#999">%</span>
      </el-form-item>
      <el-form-item label="EPS季增≥">
        <el-input-number v-model="filters.eps_qoq_min" :step="10" controls-position="right" />
        <span style="margin-left:6px;color:#999">%</span>
      </el-form-item>
      <el-form-item label="盈餘加速">
        <el-switch v-model="filters.eps_accel" />
        <span style="margin-left:6px;color:#999">連兩季EPS季增</span>
      </el-form-item>
      <el-form-item label="年增加速">
        <el-switch v-model="filters.eps_yoy_accel" />
        <span style="margin-left:6px;color:#999">年增率逐季擴大</span>
      </el-form-item>
      <el-form-item label="毛利率≥"><el-input-number v-model="filters.gross_margin_min" controls-position="right" /></el-form-item>
      <el-form-item label="毛利率季增≥">
        <el-input-number v-model="filters.gross_margin_chg_min" :step="0.5" :precision="1" controls-position="right" />
        <span style="margin-left:6px;color:#999">百分點</span>
      </el-form-item>
      <el-form-item label="營益率≥"><el-input-number v-model="filters.op_margin_min" controls-position="right" /></el-form-item>

      <el-divider>估值</el-divider>
      <el-form-item label="本益比≤"><el-input-number v-model="filters.per_max" controls-position="right" /></el-form-item>
      <el-form-item label="PER位階≤">
        <el-input-number v-model="filters.per_pctile_max" :min="0" :max="100" :step="10" controls-position="right" />
        <span style="margin-left:6px;color:#999">近3年百分位，低=便宜</span>
      </el-form-item>
      <el-form-item label="殖利率≥"><el-input-number v-model="filters.dividend_yield_min" :step="0.5" controls-position="right" /></el-form-item>

      <el-divider>籌碼</el-divider>
      <el-form-item label="法人20日≥">
        <el-input-number v-model="filters.inst_net_20d_min" :step="1000000" controls-position="right" />
        <span style="margin-left:6px;color:#999">股</span>
      </el-form-item>
      <el-form-item label="千張大戶%≥"><el-input-number v-model="filters.big1000_pct_min" controls-position="right" /></el-form-item>
      <el-form-item label="大戶連N週增">
        <el-input-number v-model="filters.big1000_up_weeks_min" :min="0" :max="26" controls-position="right" />
        <span style="margin-left:6px;color:#999">週</span>
      </el-form-item>
      <el-form-item label="散戶佔比變化≤">
        <el-input-number v-model="filters.retail_chg_max" :step="0.1" :precision="1" controls-position="right" />
        <span style="margin-left:6px;color:#999">0=散戶減少</span>
      </el-form-item>
      <el-form-item label="融資使用率≤">
        <el-input-number v-model="filters.margin_util_max" :min="0" :max="100" :step="5" controls-position="right" />
        <span style="margin-left:6px;color:#999">%，低=籌碼乾淨</span>
      </el-form-item>
      <el-form-item label="券資比≥">
        <el-input-number v-model="filters.short_margin_ratio_min" :step="5" controls-position="right" />
        <span style="margin-left:6px;color:#999">%，高=軋空題材</span>
      </el-form-item>
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
