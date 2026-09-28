// Thin fetch wrapper. Relative URLs work in dev (Vite proxies /api to the
// backend) and in production (FastAPI serves the built assets itself).

async function get(path, params) {
  const query = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.append(key, value)
  })
  const suffix = query.toString() ? `?${query}` : ''
  const response = await fetch(`/api${path}${suffix}`)
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail || detail } catch { /* keep statusText */ }
    throw new Error(`${response.status}: ${detail}`)
  }
  return response.json()
}

export const api = {
  stats: () => get('/stats'),
  config: () => get('/config'),
  meetings: (filters) => get('/meetings', filters),
  meeting: (id) => get(`/meetings/${id}`),
  conflicts: (filters) => get('/conflicts', filters),
  reviewQueue: () => get('/review-queue'),
  dataQuality: () => get('/data-quality'),
  resync: async () => {
    const response = await fetch('/api/sync', { method: 'POST' })
    if (!response.ok) throw new Error('Re-sync failed')
    return response.json()
  },
}
