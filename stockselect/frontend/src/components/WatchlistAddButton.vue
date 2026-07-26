<script setup>
// 加入自選股按鈕：任何搜尋結果列可用。點★ 開對話框選（或新增）分類後加入。
// row 需含 stock_id / name；若有 breakout/pattern/pattern_name 會一併存為型態快照。
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getWatchCategories, addWatchCategory, addWatchItem } from '../api'

const props = defineProps({
  row: { type: Object, required: true },
  size: { type: String, default: 'small' },
})

const show = ref(false)
const cats = ref([])
const catId = ref(null)
const newName = ref('')
const loading = ref(false)

async function open() {
  show.value = true
  try {
    cats.value = await getWatchCategories()
    if (cats.value.length && catId.value == null) catId.value = cats.value[0].id
  } catch (e) {
    ElMessage.error('讀取分類失敗')
  }
}

function snapshotOf(r) {
  if (!r.breakout && !r.pattern) return null      // 選股器/手動加入無型態
  return { breakout: r.breakout || null, pattern: r.pattern || null, pattern_name: r.pattern_name || null }
}

async function confirm() {
  loading.value = true
  try {
    let cid = catId.value
    const nm = newName.value.trim()
    if (nm) {                                       // 有填新分類名 → 先建再用
      const c = await addWatchCategory(nm)
      cid = c.id
    }
    if (!cid) { ElMessage.warning('請選擇或新增分類'); return }
    await addWatchItem({ category_id: cid, stock_id: props.row.stock_id, snapshot: snapshotOf(props.row) })
    ElMessage.success(`已加入自選股：${props.row.name || props.row.stock_id}`)
    show.value = false
    newName.value = ''
  } catch (e) {
    ElMessage.error('加入失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <span @click.stop>
    <el-button :size="size" text bg circle title="加入自選股" @click="open">★</el-button>
    <el-dialog v-model="show" title="加入自選股" width="360px" append-to-body>
      <div style="margin-bottom: 10px">個股：<b>{{ row.stock_id }} {{ row.name }}</b></div>
      <el-select v-model="catId" placeholder="選擇分類" style="width: 100%; margin-bottom: 10px"
                 :disabled="!!newName.trim()">
        <el-option v-for="c in cats" :key="c.id" :label="`${c.name}（${c.n}）`" :value="c.id" />
      </el-select>
      <el-input v-model="newName" placeholder="或新增分類名稱（填了就用新分類）" clearable maxlength="60" />
      <template #footer>
        <el-button @click="show = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="confirm">加入</el-button>
      </template>
    </el-dialog>
  </span>
</template>
