import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const getStrategies = () => api.get('/strategies').then((r) => r.data)
export const searchStocks = (q) => api.get('/search', { params: { q } }).then((r) => r.data)
export const getMarketOverview = () => api.get('/market/overview').then((r) => r.data)
export const getMarketIndex = (days = 120) =>
  api.get('/market/index', { params: { days } }).then((r) => r.data)
export const getMovers = (type = 'gainers', limit = 15) =>
  api.get('/market/movers', { params: { type, limit } }).then((r) => r.data)
export const runScreen = (payload) => api.post('/screen', payload).then((r) => r.data)
export const getStock = (id) => api.get(`/stock/${id}`).then((r) => r.data)
export const getPrices = (id, { tf = 'D', bars = 250, adj = 1 } = {}) =>
  api.get(`/stock/${id}/prices`, { params: { tf, bars, adj } }).then((r) => r.data)
export const getChips = (id, days = 60) =>
  api.get(`/stock/${id}/chips`, { params: { days } }).then((r) => r.data)
export const getFundamentals = (id) =>
  api.get(`/stock/${id}/fundamentals`).then((r) => r.data)

export default api
