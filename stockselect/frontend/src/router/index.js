import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/screener', name: 'screener', component: () => import('../views/ScreenerView.vue') },
    { path: '/themes', name: 'themes', component: () => import('../views/ThemeView.vue') },
    { path: '/patterns', name: 'patterns', component: () => import('../views/PatternScreenerView.vue') },
    { path: '/breakout', name: 'breakout', component: () => import('../views/BreakoutView.vue') },
    { path: '/breakout-decision', name: 'breakout-decision', component: () => import('../views/BreakoutDecisionView.vue') },
    { path: '/price-action', name: 'price-action', component: () => import('../views/PriceActionDecisionView.vue') },
    { path: '/toppattern', name: 'toppattern', component: () => import('../views/TopPatternView.vue') },
    { path: '/continuation', name: 'continuation', component: () => import('../views/ContinuationView.vue') },
    { path: '/near-breakout', name: 'near-breakout', component: () => import('../views/NearBreakoutView.vue') },
    { path: '/growth', name: 'growth', component: () => import('../views/GrowthView.vue') },
    { path: '/backtest', name: 'backtest', component: () => import('../views/BacktestView.vue') },
    { path: '/watchlist', name: 'watchlist', component: () => import('../views/WatchlistView.vue') },
    { path: '/portfolio', name: 'portfolio', component: () => import('../views/PortfolioView.vue') },
    { path: '/review', name: 'review', component: () => import('../views/TradeReviewView.vue') },
    { path: '/drill', name: 'drill', component: () => import('../views/DrillView.vue') },
    { path: '/stock/:id', name: 'stock', component: () => import('../views/StockDetailView.vue') },
  ],
})
