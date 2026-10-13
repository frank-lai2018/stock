<script setup>
defineProps({ config: { type: Object, required: true } })
</script>

<template>
  <el-collapse class="lin-guide">
    <el-collapse-item title="強哥選股條件・圖卡研究版｜查看條件定義" name="lin-rules">
      <p>{{ config.source }}。日線型態或週線前高壓力帶，三種買點擇一；「提前觀察」仍需等待買點確認，不配置部位。</p>
      <table class="rules-table">
        <thead><tr><th>條件</th><th>定義與用途</th></tr></thead>
        <tbody><tr v-for="rule in config.rules" :key="rule.item"><th>{{ rule.item }}</th><td>{{ rule.rule }}</td></tr></tbody>
      </table>
    </el-collapse-item>
  </el-collapse>
</template>

<style scoped>
.lin-guide { margin: 0 0 14px; }
p { color: #606266; margin: 4px 0 12px; line-height: 1.7; }
.rules-table { width: 100%; border-collapse: collapse; color: #606266; line-height: 1.7; }
th, td { border: 1px solid #ebeef5; padding: 9px 12px; text-align: left; vertical-align: top; }
th { width: 135px; background: #f5f7fa; font-weight: 600; }
</style>
