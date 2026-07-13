<script setup>
import { useRoute } from 'vue-router'
import SearchBox from './components/SearchBox.vue'
const route = useRoute()
</script>

<template>
  <el-container style="height: 100vh">
    <el-header style="display: flex; align-items: center; gap: 16px; border-bottom: 1px solid #e4e7ed">
      <h2 style="margin: 0; white-space: nowrap">📈 台股選股系統</h2>
      <el-menu mode="horizontal" :router="true" :default-active="route.path" style="border: 0; flex: 1; min-width: 0">
        <el-menu-item index="/">首頁</el-menu-item>
        <el-menu-item index="/screener">選股器</el-menu-item>
      </el-menu>
      <SearchBox />
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>
