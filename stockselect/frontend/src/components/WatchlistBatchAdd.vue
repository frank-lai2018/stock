<script setup>
// 批次加入自選股：接收已勾選的列，選（或新增）分類後一次加入。加入完成 emit('done') 讓表格清除勾選。
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getWatchCategories, addWatchCategory, addWatchItemsBulk } from '../api'

const props = defineProps({ rows: { type: Array, default: () => [] } })
const emit = defineEmits(['done'])

const show = ref(false)
const cats = ref([])
const catId = ref(null)
const newName = ref('')
const loading = ref(false)

async function open() {
  if (!props.rows.length) { ElMessage.warning('尚未勾選任何股票'); return }
  show.value = true
  try {
    cats.value = await getWatchCategories()
    if (cats.value.length && catId.value == null) catId.value = cats.value[0].id
  } catch (e) {
    ElMessage.error('讀取分類失敗')
  }
}

function snapshotOf(r) {
  if (!r.breakout && !r.pattern) return null
  return { breakout: r.breakout || null, pattern: r.pattern || null, pattern_name: r.pattern_name || null }
}

async function confirm() {
  loading.value = true
  try {
    let cid = catId.value
    const nm = newName.value.trim()
    if (nm) {
      const c = await addWatchCategory(nm)
      cid = c.id
    }
    if (!cid) { ElMessage.warning('請選擇或新增分類'); return }
    const items = props.rows.map((r) => ({ stock_id: r.stock_id, snapshot: snapshotOf(r) }))
    const res = await addWatchItemsBulk({ category_id: cid, items })
    ElMessage.success(`已加入 ${res.added} 檔` + (res.skipped ? `，略過 ${res.skipped} 檔` : ''))
    show.value = false
    newName.value = ''
    emit('done')
  } catch (e) {
    ElMessage.error('加入失敗：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <span @click.stop>
    <el-button type="primary" size="small" @click="open">加入自選股（{{ rows.length }}）</el-button>
    <el-dialog v-model="show" title="批次加入自選股" width="360px" append-to-body>
      <div style="margin-bottom: 10px">已勾選 <b>{{ rows.length }}</b> 檔</div>
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
