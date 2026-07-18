import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/screener', name: 'screener', component: () => import('../views/ScreenerView.vue') },
    { path: '/patterns', name: 'patterns', component: () => import('../views/PatternScreenerView.vue') },
    { path: '/portfolio', name: 'portfolio', component: () => import('../views/PortfolioView.vue') },
    { path: '/stock/:id', name: 'stock', component: () => import('../views/StockDetailView.vue') },
  ],
})
