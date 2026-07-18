import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getStrategies = () => api.get('/strategies').then((r) => r.data)
export const searchStocks = (q) => api.get('/search', { params: { q } }).then((r) => r.data)
export const getMarketOverview = () => api.get('/market/overview').then((r) => r.data)
export const getMarketIndex = (days = 120, index_id = 'TAIEX') =>
  api.get('/market/index', { params: { days, index_id } }).then((r) => r.data)
export const getMovers = (type = 'gainers', limit = 15) =>
  api.get('/market/movers', { params: { type, limit } }).then((r) => r.data)
export const getSectors = (market = '上市') =>
  api.get('/market/sectors', { params: { market } }).then((r) => r.data)
export const getMoneyflow = (market = '上市') =>
  api.get('/market/moneyflow', { params: { market } }).then((r) => r.data)
export const runScreen = (payload) => api.post('/screen', payload).then((r) => r.data)
export const getStock = (id) => api.get(`/stock/${id}`).then((r) => r.data)
export const getPrices = (id, { tf = 'D', bars = 250, adj = 1 } = {}) =>
  api.get(`/stock/${id}/prices`, { params: { tf, bars, adj } }).then((r) => r.data)
export const getChips = (id, days = 60) =>
  api.get(`/stock/${id}/chips`, { params: { days } }).then((r) => r.data)
export const getMargin = (id, tf = 'D', bars = 60) =>
  api.get(`/stock/${id}/margin`, { params: { tf, bars } }).then((r) => r.data)
export const getFundamentals = (id) =>
  api.get(`/stock/${id}/fundamentals`).then((r) => r.data)
export const getPatterns = () => api.get('/patterns').then((r) => r.data)
export const screenPattern = (pattern, limit = 100) =>
  api.get('/screen/pattern', { params: { pattern, limit } }).then((r) => r.data)
export const getStockPatterns = (id, days = 90) =>
  api.get(`/stock/${id}/patterns`, { params: { days } }).then((r) => r.data)
export const getLevels = (id, bars = 120) =>
  api.get(`/stock/${id}/levels`, { params: { bars } }).then((r) => r.data)
export const getStockVpa = (id, days = 90) =>
  api.get(`/stock/${id}/vpa`, { params: { days } }).then((r) => r.data)

export default api
