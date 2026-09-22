export function formatVideoTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const minutes = Math.floor(total / 60)
  const rest = String(total % 60).padStart(2, '0')
  if (minutes < 60) return `${minutes}:${rest}`
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${rest}`
}

function bounds(summary) {
  return { start: Number(summary.start_time), end: Number(summary.end_time) }
}

export function resolveReviewRange(reference, summaries = []) {
  const start = Number(reference?.start_time)
  const records = summaries.filter(s => {
    const b = bounds(s)
    return Number.isFinite(b.start) && Number.isFinite(b.end) && b.end > b.start
  }).sort((a, b) => a.start_time - b.start_time)
  if (!Number.isFinite(start) || start < 0 || !records.length) return null
  const lastEnd = Math.max(...records.map(s => Number(s.end_time)))
  const pointWindow = records.find(s => start >= Number(s.start_time) && start < Number(s.end_time))
    || (start === lastEnd ? records.at(-1) : null)
  if (!pointWindow) return null
  if (reference.end_time == null) {
    return { start_time: Number(pointWindow.start_time), end_time: Number(pointWindow.end_time), window_id: pointWindow.window_id }
  }
  const end = Number(reference.end_time)
  if (!Number.isFinite(end) || end <= start || end > lastEnd) return null
  const overlapping = records.filter(s => Number(s.start_time) < end && Number(s.end_time) > start)
  // Never turn timestamps outside available analysis coverage into a playable citation.
  let covered = start
  for (const s of overlapping) {
    if (Number(s.start_time) > covered + 0.01) return null
    covered = Math.max(covered, Number(s.end_time))
  }
  if (covered < end) return null
  return {
    start_time: start, end_time: end,
    ...(overlapping.length === 1 ? { window_id: overlapping[0].window_id } : {}),
  }
}

function parseTime(text) {
  const parts = text.split(/[:：]/).map(Number)
  if (parts.at(-1) >= 60 || (parts.length === 3 && parts[1] >= 60)) return null
  return parts.reduce((total, value) => total * 60 + value, 0)
}

export function tokenizeChatTimestamps(value, summaries = []) {
  const text = String(value || '')
  const clock = '\\d{1,4}[:：]\\d{2}(?:[:：]\\d{2})?'
  const pattern = new RegExp(`(?<![\\w/:：.])(${clock})(?:\\s*(?:[-–—~～至到]|to)\\s*(${clock}))?(?![\\d:：])`, 'gi')
  const parts = []
  let offset = 0
  for (const match of text.matchAll(pattern)) {
    // URLs are not video-time references, even when a query parameter looks like one.
    const word = text.slice(text.lastIndexOf(' ', match.index - 1) + 1, match.index)
    if (/https?:\/\/\S*$/.test(word)) continue
    const start = parseTime(match[1])
    const end = match[2] ? parseTime(match[2]) : undefined
    if (start == null || end === null) continue
    const reference = resolveReviewRange({ start_time: start, end_time: end }, summaries)
    if (!reference) continue
    if (match.index > offset) parts.push({ text: text.slice(offset, match.index) })
    parts.push({ text: match[0], reference })
    offset = match.index + match[0].length
  }
  if (offset < text.length) parts.push({ text: text.slice(offset) })
  return parts.length ? parts : [{ text }]
}
