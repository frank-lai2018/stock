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
        <el-menu-item index="/patterns">型態選股</el-menu-item>
        <el-menu-item index="/breakout">型態突破</el-menu-item>
        <el-menu-item index="/toppattern">頭部反轉</el-menu-item>
        <el-menu-item index="/continuation">整理突破</el-menu-item>
        <el-menu-item index="/near-breakout">接近突破</el-menu-item>
        <el-menu-item index="/growth">財報成長</el-menu-item>
        <el-menu-item index="/backtest">型態回測</el-menu-item>
        <el-menu-item index="/watchlist">自選股</el-menu-item>
        <el-menu-item index="/portfolio">持股診斷</el-menu-item>
        <el-menu-item index="/review">交易復盤</el-menu-item>
        <el-menu-item index="/drill">看圖練習</el-menu-item>
      </el-menu>
      <SearchBox />
    </el-header>
    <el-main>
      <router-view :key="route.fullPath" />
    </el-main>
  </el-container>
</template>
