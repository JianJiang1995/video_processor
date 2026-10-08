// Single-flight, source-keyed translations: a revised analysis can never reuse
// the English wording of an older revision of the same window.
export function createTextTranslator({ translate, changed = () => {}, maxEntries = 2048 }) {
  const cache = new Map()
  const queue = new Map()
  let active = false
  let scheduled = false
  let sequence = 0
  const keyOf = (text, target) => JSON.stringify([target, text])
  const pump = async () => {
    scheduled = false
    if (active || !queue.size) return
    const job = [...queue.values()].sort((a, b) => b.priority - a.priority || a.order - b.order)[0]
    queue.delete(job.key)
    active = true
    try {
      const text = await translate(job.text, job.target)
      if (!text || (job.target === 'en' && /[\u3400-\u9fff]/.test(text))) throw Error('Incomplete translation')
      cache.set(job.key, { status: 'ready', text })
    } catch {
      cache.set(job.key, { status: 'failed', text: '' })
    } finally {
      active = false
      for (const [key, value] of cache) {
        if (cache.size <= maxEntries) break
        if (value.status !== 'pending') cache.delete(key)
      }
      changed()
      schedule()
    }
  }
  const schedule = () => {
    if (!scheduled && !active && queue.size) {
      scheduled = true
      queueMicrotask(pump)
    }
  }
  return {
    request(text, target, priority = 0) {
      const key = keyOf(text, target)
      if (!cache.has(key)) {
        cache.set(key, { status: 'pending', text: '' })
        queue.set(key, { key, text, target, priority, order: sequence++ })
      } else if (queue.has(key)) {
        queue.get(key).priority = Math.max(queue.get(key).priority, priority)
      }
      schedule()
      return cache.get(key)
    },
    retry() {
      for (const [key, state] of cache) if (state.status === 'failed') cache.delete(key)
      changed()
    },
    clearPending() {
      for (const key of queue.keys()) cache.delete(key)
      queue.clear()
    },
    hasFailures: () => [...cache.values()].some(state => state.status === 'failed'),
  }
}
