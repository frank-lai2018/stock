<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { searchStocks } from '../api'

const router = useRouter()
const q = ref('')

async function query(qs, cb) {
  const s = (qs || '').trim()
  if (!s) return cb([])
  try {
    const rows = await searchStocks(s)
    cb(rows.map((r) => ({ ...r, value: `${r.stock_id} ${r.name}` })))
  } catch {
    cb([])
  }
}

function onSelect(item) {
  if (item && item.stock_id) router.push(`/stock/${item.stock_id}`)
  q.value = ''
}
</script>

<template>
  <el-autocomplete
    v-model="q"
    :fetch-suggestions="query"
    :debounce="250"
    :trigger-on-focus="false"
    clearable
    placeholder="搜尋代碼 / 名稱"
    style="width: 260px"
    @select="onSelect"
  >
    <template #default="{ item }">
      <div style="display: flex; justify-content: space-between; gap: 12px; align-items: center">
        <span><b style="color: #ea4c4c">{{ item.stock_id }}</b>&nbsp;{{ item.name }}</span>
        <span style="color: #999; font-size: 12px">
          {{ item.market }}<template v-if="item.industry"> / {{ item.industry }}</template>
        </span>
      </div>
    </template>
  </el-autocomplete>
</template>
