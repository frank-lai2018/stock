<script setup>
// 看圖練習器：隨機抽一段被截斷的 K 線 → 先判斷 → 才揭曉 → 累積戰績。
// 刻意不顯示股票代號與真實日期（時間軸由後端換成合成的），避免「認出這是哪一段」而作弊。
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { init, dispose } from 'klinecharts'
import { ElMessage } from 'element-plus'
import { newDrill, answerDrill, revealDrill, getDrillStats, getDrillHistory } from '../api'

const UP = '#EA4C4C'          // 漲：紅（台股慣例）
const DOWN = '#3F9E5A'

const el = ref(null)
let chart = null
let histBars = []             // 題目那段（截斷到 T）

const drill = ref(null)       // { id, bars, horizon, last_close }
const result = ref(null)      // 揭曉後的結果
const loading = ref(false)
const stats = ref(null)
const history = ref([])
const showHistory = ref(false)

// 作答表單
const bars = ref(120)
const horizon = ref(20)
const ans = ref({ dir: '', support: null, resistance: null, entry: false, stop: null, confidence: 3, note: '' })

function resetAns() {
  ans.value = { dir: '', support: null, resistance: null, entry: false, stop: null, confidence: 3, note: '' }
}

function draw(data) {
  if (!chart) return
  chart.applyNewData(data)
}

async function next() {
  loading.value = true
  result.value = null
  resetAns()
  try {
    const d = await newDrill(bars.value, horizon.value)
    drill.value = d
    histBars = d.bars
    await nextTick()
    draw(histBars)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '抽題失敗')
  } finally {
    loading.value = false
  }
}

async function submit() {
  if (!ans.value.dir) return ElMessage.warning('請先選一個方向')
  loading.value = true
  try {
    await answerDrill(drill.value.id, ans.value)
    const r = await revealDrill(drill.value.id)
    result.value = r
    draw(histBars.concat(r.future))            // 接上未來，分隔處由下方文字說明
    await loadStats()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '送出失敗')
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try { stats.value = await getDrillStats() } catch (e) { /* 尚無資料不影響練習 */ }
}

async function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value) {
    try { history.value = await getDrillHistory(50) } catch (e) { history.value = [] }
  }
}

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(2)}%`)
const rate = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const clr = (v) => (v == null ? '' : v > 0 ? UP : v < 0 ? DOWN : '#888')

onMounted(async () => {
  chart = init(el.value)
  chart.setStyles({
    candle: {
      bar: { upColor: UP, downColor: DOWN, noChangeColor: '#888',
             upBorderColor: UP, downBorderColor: DOWN, upWickColor: UP, downWickColor: DOWN },
    },
    xAxis: { tickText: { show: false } },       // 藏掉時間軸文字：合成日期沒有意義、只會分心
  })
  chart.createIndicator({ name: 'MA', calcParams: [5, 20, 60] }, false, { id: 'candle_pane' })
  chart.createIndicator('VOL', false)
  await next()
  await loadStats()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (el.value) dispose(el.value)
})
function onResize() { chart?.resize() }
</script>

<template>
  <div style="padding: 12px">
    <el-card shadow="never" style="margin-bottom: 10px">
      <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
        <b style="font-size: 16px">🎯 看圖練習器</b>
        <span style="color: #888; font-size: 13px">
          隨機一段被截斷的 K 線（不顯示股票與日期）→ 先判斷 → 才揭曉。練的是「當下敢不敢出手」，不是事後諸葛。
        </span>
        <div style="flex: 1"></div>
        <span style="color: #666; font-size: 13px">歷史根數</span>
        <el-input-number v-model="bars" :min="40" :max="400" :step="20" size="small"
                         controls-position="right" style="width: 110px" />
        <span style="color: #666; font-size: 13px">看未來</span>
        <el-input-number v-model="horizon" :min="1" :max="120" :step="5" size="small"
                         controls-position="right" style="width: 100px" />
        <el-button size="small" @click="toggleHistory">{{ showHistory ? '收起紀錄' : '作答紀錄' }}</el-button>
        <el-button type="primary" :loading="loading" @click="next">下一題 →</el-button>
      </div>
    </el-card>

    <el-row :gutter="10">
      <el-col :span="17">
        <el-card shadow="never" body-style="padding:8px">
          <div ref="el" style="width: 100%; height: 460px"></div>
          <div v-if="result" style="padding: 8px 4px 0; font-size: 13px">
            <el-tag :type="result.correct ? 'success' : 'danger'" effect="dark" size="large">
              {{ result.correct ? '✔ 判斷正確' : '✘ 判斷錯誤' }}
            </el-tag>
            <span style="margin-left: 12px">
              <b>{{ result.stock_id }} {{ result.name }}</b>
              <span style="color: #888">（{{ result.industry }}）截斷日 {{ result.as_of }}</span>
            </span>
            <span style="margin-left: 12px">
              {{ drill.horizon }} 日後
              <b :style="{ color: clr(result.fwd_ret) }">{{ pct(result.fwd_ret) }}</b>
              <span style="color: #888">
                ｜期間最高 <span :style="{ color: clr(result.fwd_high_pct) }">{{ pct(result.fwd_high_pct) }}</span>
                、最低 <span :style="{ color: clr(result.fwd_low_pct) }">{{ pct(result.fwd_low_pct) }}</span>
              </span>
            </span>
            <div style="color: #999; margin-top: 4px">
              圖上右側 {{ drill.horizon }} 根即為揭曉的未來走勢。
              <router-link :to="`/stock/${result.stock_id}`" target="_blank">看這檔的完整線圖 →</router-link>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="7">
        <el-card shadow="never" header="你的判斷" body-style="padding:12px">
          <el-form label-width="72px" size="small" :disabled="!!result || loading">
            <el-form-item label="方向">
              <el-radio-group v-model="ans.dir">
                <el-radio-button value="bull">看多</el-radio-button>
                <el-radio-button value="neutral">盤整</el-radio-button>
                <el-radio-button value="bear">看空</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="信心">
              <el-rate v-model="ans.confidence" :max="5" show-score score-template="{value} 分" />
            </el-form-item>
            <el-form-item label="支撐">
              <el-input-number v-model="ans.support" :precision="2" :step="1" controls-position="right"
                               style="width: 130px" />
            </el-form-item>
            <el-form-item label="壓力">
              <el-input-number v-model="ans.resistance" :precision="2" :step="1" controls-position="right"
                               style="width: 130px" />
            </el-form-item>
            <el-form-item label="會進場">
              <el-switch v-model="ans.entry" />
              <el-input-number v-if="ans.entry" v-model="ans.stop" :precision="2" :step="1"
                               controls-position="right" placeholder="停損" style="width: 120px; margin-left: 8px" />
            </el-form-item>
            <el-form-item label="理由">
              <el-input v-model="ans.note" type="textarea" :rows="2" maxlength="300" show-word-limit
                        placeholder="為什麼這樣判斷？寫下來，之後回顧才知道自己錯在哪" />
            </el-form-item>
          </el-form>
          <el-button v-if="!result" type="success" style="width: 100%" :loading="loading" @click="submit">
            送出並揭曉
          </el-button>
          <el-button v-else type="primary" style="width: 100%" :loading="loading" @click="next">
            下一題 →
          </el-button>
          <div style="color: #bbb; font-size: 12px; margin-top: 6px">
            送出後不能改答案——這是刻意的，不然統計沒意義。
          </div>
        </el-card>

        <el-card v-if="stats?.n" shadow="never" header="累積戰績" style="margin-top: 10px" body-style="padding:12px">
          <div style="font-size: 13px">
            共 <b>{{ stats.n }}</b> 題，正確率
            <b style="font-size: 18px">{{ rate(stats.accuracy) }}</b>
            <span style="color: #888">｜平均報酬 {{ pct(stats.avg_ret) }}</span>
          </div>
          <el-divider style="margin: 8px 0" />
          <div style="font-size: 13px; color: #666">依方向</div>
          <div v-for="d in stats.by_dir" :key="d.dir" style="font-size: 13px">
            {{ { bull: '看多', bear: '看空', neutral: '盤整' }[d.dir] || d.dir }}：
            {{ rate(d.accuracy) }} <span style="color: #999">（{{ d.n }} 題）</span>
          </div>
          <el-divider style="margin: 8px 0" />
          <div style="font-size: 13px; color: #666">依信心分層</div>
          <div v-for="c in stats.by_confidence" :key="c.confidence" style="font-size: 13px">
            {{ c.confidence }} 分：{{ rate(c.accuracy) }} <span style="color: #999">（{{ c.n }} 題）</span>
          </div>
          <div style="color: #bbb; font-size: 12px; margin-top: 8px; line-height: 1.5">
            信心分層最值得看：若高信心的正確率沒有比低信心高，
            代表你的「自信」沒有資訊量——那是最該修正的地方。
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card v-if="showHistory" shadow="never" header="作答紀錄" style="margin-top: 10px" body-style="padding:0">
      <el-table :data="history" size="small" height="360">
        <el-table-column prop="stock_id" label="代號" width="80" />
        <el-table-column prop="name" label="名稱" width="100" />
        <el-table-column prop="as_of" label="截斷日" width="110" />
        <el-table-column label="判斷" width="80">
          <template #default="{ row }">{{ { bull: '看多', bear: '看空', neutral: '盤整' }[row.ans_dir] }}</template>
        </el-table-column>
        <el-table-column prop="ans_confidence" label="信心" width="64" />
        <el-table-column label="結果" width="72">
          <template #default="{ row }">
            <el-tag :type="row.correct ? 'success' : 'danger'" size="small">{{ row.correct ? '對' : '錯' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="報酬" width="88" sortable :sort-method="(a, b) => a.fwd_ret - b.fwd_ret">
          <template #default="{ row }"><span :style="{ color: clr(row.fwd_ret) }">{{ pct(row.fwd_ret) }}</span></template>
        </el-table-column>
        <el-table-column prop="ans_note" label="當時的理由" min-width="220" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>
