import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { listAnalysisHistory, rememberAnalysis } = require('../electron/analysisHistory.cjs')

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'analysis-history-'))
  t.after(() => fs.rm(root, { recursive: true, force: true }))
  return root
}
async function bundle(root, name, sessionId = name, extra = {}) {
  const directory = path.join(root, name)
  await fs.mkdir(directory, { recursive: true })
  await fs.writeFile(path.join(directory, 'video.mp4'), 'media')
  // A catalogue must stat, not parse, the potentially very large window payload.
  await fs.writeFile(path.join(directory, 'summaries.json'), 'not loaded by the catalogue')
  await fs.writeFile(path.join(directory, 'summary.md'), '# Report')
  const specPath = path.join(directory, 'analysis.replay.json')
  await fs.writeFile(specPath, JSON.stringify({ video_path: 'video.mp4', summaries_path: 'summaries.json',
    report_path: 'summary.md', session: { session_id: sessionId, video_name: `${name}.mp4`, duration: 623 }, ...extra }))
  await fs.writeFile(path.join(directory, 'export_manifest.json'), JSON.stringify({
    session: { session_id: sessionId }, window_count: 125,
  }))
  return specPath
}

test('discovers relative-path bundles without reading analysis or video content', async t => {
  const root = await fixture(t)
  const specPath = await bundle(root, 'recordings/day/video')
  const result = await listAnalysisHistory({ roots: [path.join(root, 'recordings')] })
  assert.equal(result.success, true)
  assert.equal(result.entries.length, 1)
  assert.equal(result.entries[0].specPath, specPath)
  assert.equal(result.entries[0].windowCount, 125)
  assert.equal(result.entries[0].duration, 623)
  assert.equal(result.entries[0].available, true)
  assert.equal(result.entries[0].reportAvailable, true)
})

test('persists imported paths outside recordings and reports missing assets', async t => {
  const root = await fixture(t)
  const specPath = await bundle(root, 'external')
  const saved = JSON.parse(JSON.stringify(rememberAnalysis([], {
    specPath, sessionId: 'external', title: 'Imported video', windowCount: 125,
  })))
  let result = await listAnalysisHistory({ roots: [], remembered: saved })
  assert.equal(result.entries[0].available, true)
  await fs.unlink(path.join(path.dirname(specPath), 'video.mp4'))
  result = await listAnalysisHistory({ remembered: saved })
  assert.equal(result.entries[0].available, false)
  assert.deepEqual(result.entries[0].missing, ['video'])
  await fs.unlink(specPath)
  result = await listAnalysisHistory({ remembered: saved })
  assert.deepEqual(result.entries[0].missing, ['spec'])
  assert.equal(result.entries[0].reportAvailable, false)
})

test('deduplicates sessions but preserves separate analysis runs of the same video', async t => {
  const root = await fixture(t)
  await bundle(root, 'old', 'one')
  const latest = await bundle(root, 'new', 'one')
  await fs.utimes(latest, new Date('2030-01-01'), new Date('2030-01-01'))
  await bundle(root, 'another-run', 'two', { title: 'same-video' })
  let result = await listAnalysisHistory({ roots: [root] })
  assert.equal(result.entries.length, 2)
  assert.equal(result.entries[0].specPath, latest)
  await fs.unlink(path.join(path.dirname(latest), 'video.mp4'))
  result = await listAnalysisHistory({ roots: [root] })
  assert.equal(result.entries.find(row => row.sessionId === 'one').available, true)
  assert.match(result.entries.find(row => row.sessionId === 'one').specPath, /old/)
})

test('a corrupt spec and symlink loop do not block valid history', async t => {
  const root = await fixture(t)
  await bundle(root, 'valid')
  await fs.writeFile(path.join(root, 'bad.replay.json'), '{bad')
  await fs.symlink(root, path.join(root, 'loop'), 'dir')
  const result = await listAnalysisHistory({ roots: [root, path.join(root, 'missing')] })
  assert.equal(result.entries.length, 1)
  assert.equal(result.skipped, 1)
  assert.equal(result.truncated, false)
})

test('does not follow unbounded trees and does not hide remembered imports beyond the scan limit', async t => {
  const root = await fixture(t)
  const specPath = await bundle(root, 'nested/deep')
  const result = await listAnalysisHistory({ roots: [root], scanOptions: { maxDepth: 0 },
    remembered: [{ specPath, sessionId: 'nested/deep' }] })
  assert.equal(result.entries.length, 1)
  assert.equal(result.truncated, true)
})

test('legacy imports need no report or manifest and registration is idempotent', async t => {
  const root = await fixture(t)
  const specPath = await bundle(root, 'legacy', 'legacy', { report_path: null })
  await fs.unlink(path.join(root, 'legacy/export_manifest.json'))
  const entry = { specPath, sessionId: 'legacy', windowCount: 12 }
  const saved = rememberAnalysis(rememberAnalysis(null, entry), entry)
  const result = await listAnalysisHistory({ remembered: saved })
  assert.equal(saved.length, 1)
  assert.equal(result.entries[0].windowCount, 12)
  assert.equal(result.entries[0].reportAvailable, false)
  assert.equal(result.entries[0].available, true)
})
