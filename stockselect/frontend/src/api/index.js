import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getStrategies = () => api.get('/strategies').then((r) => r.data)
export const searchStocks = (q) => api.get('/search', { params: { q } }).then((r) => r.data)
export const getMarketOverview = () => api.get('/market/overview').then((r) => r.data)
export const getMarketIndex = (days = 120, index_id = 'TAIEX') =>
  api.get('/market/index', { params: { days, index_id } }).then((r) => r.data)
export const getMovers = (type = 'gainers', limit = 15) =>
  api.get('/market/movers', { params: { type, limit } }).then((r) => r.data)
export const getMarketMargin = (days = 20, market = 'ALL') =>
  api.get('/market/margin', { params: { days, market } }).then((r) => r.data)
export const getSectors = (market = '上市') =>
  api.get('/market/sectors', { params: { market } }).then((r) => r.data)
export const getMoneyflow = (market = '上市') =>
  api.get('/market/moneyflow', { params: { market } }).then((r) => r.data)
export const runScreen = (payload) => api.post('/screen', payload).then((r) => r.data)
export const screenBreakout = (params = {}) =>
  api.get('/screen/pattern-breakout', { params }).then((r) => r.data)
export const getBreakoutPatterns = (group = 'bottom') =>
  api.get('/screen/breakout-patterns', { params: { group } }).then((r) => r.data)
export const getPatternBacktest = () => api.get('/screen/backtest').then((r) => r.data)
export const getStock = (id) => api.get(`/stock/${id}`).then((r) => r.data)
export const getPrices = (id, { tf = 'D', bars = 250, adj = 1 } = {}) =>
  api.get(`/stock/${id}/prices`, { params: { tf, bars, adj } }).then((r) => r.data)
export const getChips = (id, days = 60) =>
  api.get(`/stock/${id}/chips`, { params: { days } }).then((r) => r.data)
export const getMargin = (id, tf = 'D', bars = 60) =>
  api.get(`/stock/${id}/margin`, { params: { tf, bars } }).then((r) => r.data)
export const getInstTrades = (id, tf = 'D', bars = 60) =>
  api.get(`/stock/${id}/inst`, { params: { tf, bars } }).then((r) => r.data)
export const getFundamentals = (id) =>
  api.get(`/stock/${id}/fundamentals`).then((r) => r.data)
export const getPatterns = () => api.get('/patterns').then((r) => r.data)
export const screenPattern = (pattern, limit = 100) =>
  api.get('/screen/pattern', { params: { pattern, limit } }).then((r) => r.data)
export const getStockPatterns = (id, days = 90) =>
  api.get(`/stock/${id}/patterns`, { params: { days } }).then((r) => r.data)
export const getLevels = (id, bars = 120, tf = 'D') =>
  api.get(`/stock/${id}/levels`, { params: { bars, tf } }).then((r) => r.data)
export const getStockVpa = (id, days = 90) =>
  api.get(`/stock/${id}/vpa`, { params: { days } }).then((r) => r.data)
export const getDividends = (id) =>
  api.get(`/stock/${id}/dividends`).then((r) => r.data)
export const getEtfInfo = (id) =>
  api.get(`/stock/${id}/etf`).then((r) => r.data)

// 持股診斷 / 交易帳
export const getPortfolio = (year) =>
  api.get('/portfolio', { params: year ? { year } : {} }).then((r) => r.data)
export const getTrades = (year) =>
  api.get('/trades', { params: year ? { year } : {} }).then((r) => r.data)
export const getStockTrades = (stockId) =>
  api.get('/trades', { params: { stock_id: stockId } }).then((r) => r.data)
export const addTrade = (t) => api.post('/trades', t).then((r) => r.data)
export const deleteTrade = (id) => api.delete(`/trades/${id}`).then((r) => r.data)

// 自選股（自建分類 + 成員）
export const getWatchCategories = () => api.get('/watchlist/categories').then((r) => r.data)
export const addWatchCategory = (name) => api.post('/watchlist/categories', { name }).then((r) => r.data)
export const renameWatchCategory = (id, name) =>
  api.put(`/watchlist/categories/${id}`, { name }).then((r) => r.data)
export const deleteWatchCategory = (id) => api.delete(`/watchlist/categories/${id}`).then((r) => r.data)
export const getWatchItems = (cid) => api.get(`/watchlist/${cid}/items`).then((r) => r.data)
export const addWatchItem = (payload) => api.post('/watchlist/items', payload).then((r) => r.data)
export const addWatchItemsBulk = (payload) =>
  api.post('/watchlist/items/bulk', payload).then((r) => r.data)
export const deleteWatchItem = (id) => api.delete(`/watchlist/items/${id}`).then((r) => r.data)

export default api
