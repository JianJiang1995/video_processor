const fs = require('node:fs/promises')
const path = require('node:path')

async function readSmallJson(file, limit = 2 * 1024 ** 2) {
  const stat = await fs.stat(file)
  if (!stat.isFile() || stat.size > limit) throw new Error('invalid_history_metadata')
  return JSON.parse(await fs.readFile(file, 'utf8'))
}

async function fileStat(file) {
  if (!file) return null
  try {
    const stat = await fs.stat(file)
    return stat.isFile() ? stat : null
  } catch { return null }
}

function assetPath(directory, value) {
  return typeof value === 'string' && value ? path.resolve(directory, value) : null
}

async function discoverReplaySpecs(roots, { maxDirectories = 2500, maxDepth = 10 } = {}) {
  const files = new Set()
  const queue = [...new Set(roots.map(root => path.resolve(root)))].map(directory => ({ directory, depth: 0 }))
  const seen = new Set()
  let truncated = false
  for (let index = 0; index < queue.length; index++) {
    if (seen.size >= maxDirectories) { truncated = true; break }
    const { directory, depth } = queue[index]
    if (seen.has(directory)) continue
    seen.add(directory)
    let entries
    try { entries = await fs.readdir(directory, { withFileTypes: true }) }
    catch { continue }
    for (const entry of entries) {
      if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue
      const target = path.join(directory, entry.name)
      if (entry.isFile() && entry.name.endsWith('.replay.json')) files.add(target)
      else if (entry.isDirectory() && !['frames', 'preview', 'node_modules', 'ui'].includes(entry.name)) {
        if (depth < maxDepth && queue.length < maxDirectories) queue.push({ directory: target, depth: depth + 1 })
        else truncated = true
      }
    }
  }
  return { files: [...files], truncated }
}

function rememberAnalysis(previous, entry) {
  const items = Array.isArray(previous) ? previous : []
  return [{ ...entry, specPath: path.resolve(entry.specPath) },
    ...items.filter(item => item?.specPath !== path.resolve(entry.specPath))].slice(0, 500)
}

async function inspectAnalysis(specPath, remembered = {}) {
  const spec = await readSmallJson(specPath)
  const directory = path.dirname(specPath)
  const sessionId = String(spec.session?.session_id || spec.session_id || '').trim()
  if (!sessionId || !spec.summaries_path || !(spec.video_path || spec.session?.video_path)) {
    throw new Error('invalid_history_spec')
  }
  const video = await fileStat(assetPath(directory, spec.video_path || spec.session?.video_path))
  const summaries = await fileStat(assetPath(directory, spec.summaries_path))
  const report = await fileStat(assetPath(directory, spec.report_path))
  const specStat = await fileStat(specPath)
  let manifest = {}
  try { manifest = await readSmallJson(path.join(directory, 'export_manifest.json'), 128 * 1024) }
  catch { /* Older and manually imported bundles need no manifest. */ }
  if (manifest.session?.session_id !== sessionId) manifest = {}
  const count = manifest.window_count ?? remembered.windowCount
  const windowCount = Number.isInteger(count) && count >= 0 ? count : null
  const missing = []
  if (!video?.size) missing.push('video')
  if (!summaries?.size) missing.push('summaries')
  return {
    specPath, sessionId,
    title: String(spec.session?.video_name || spec.title || path.basename(spec.video_path || spec.session?.video_path)),
    sourceName: typeof manifest.source_file === 'string' ? path.basename(manifest.source_file) : '',
    duration: Math.max(0, Number(spec.session?.duration || spec.duration) || 0),
    updatedAt: new Date(Math.max(specStat?.mtimeMs || 0, summaries?.mtimeMs || 0)).toISOString(),
    windowCount, reportAvailable: Boolean(report?.size), available: missing.length === 0, missing,
  }
}

async function listAnalysisHistory({ roots = [], remembered = [], scanOptions } = {}) {
  const scan = await discoverReplaySpecs(roots, scanOptions)
  const saved = new Map((Array.isArray(remembered) ? remembered : [])
    .filter(item => typeof item?.specPath === 'string')
    .map(item => [path.resolve(item.specPath), item]))
  const bySession = new Map()
  let skipped = 0
  for (const specPath of new Set([...saved.keys(), ...scan.files])) {
    let entry
    try { entry = await inspectAnalysis(specPath, saved.get(specPath)) }
    catch {
      const old = saved.get(specPath)
      if (!old?.sessionId) { skipped++; continue }
      entry = { ...old, specPath, available: false, reportAvailable: false, missing: ['spec'] }
    }
    const old = bySession.get(entry.sessionId)
    if (!old || (entry.available && !old.available) || (entry.available === old.available && entry.updatedAt > old.updatedAt)) {
      bySession.set(entry.sessionId, entry)
    }
  }
  // Only small metadata is read here. Full window analysis is loaded on click.
  const entries = [...bySession.values()].sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)))
  return { success: true, entries, skipped, truncated: scan.truncated }
}

module.exports = { discoverReplaySpecs, listAnalysisHistory, rememberAnalysis }
