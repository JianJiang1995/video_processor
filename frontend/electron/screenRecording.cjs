const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const { randomUUID } = require('node:crypto')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const recordingOptions = import('./recordingOptions.mjs')

const GB = 1024 ** 3

async function recordingCapacity(directory) {
  await fs.mkdir(directory, { recursive: true })
  const stat = await fs.statfs(directory)
  return {
    diskBytes: Number(stat.bavail) * Number(stat.bsize),
    memoryBytes: os.freemem(),
    cpuLoad: os.loadavg()[0] / Math.max(1, os.cpus().length),
  }
}

function recordingAdmission(capacity, automatic) {
  if (capacity.diskBytes < 2 * GB) return 'disk'
  if (automatic && (capacity.memoryBytes < 2 * GB || capacity.cpuLoad > 0.8)) return 'load'
  return null
}

class RecordingFile {
  constructor(directory, { capacity = recordingCapacity, index = true } = {}) {
    this.directory = directory
    this.capacity = capacity
    this.index = index
    this.active = null
    this.opening = false
  }

  async begin({ sessionId, automatic = false, fps } = {}) {
    if (this.active || this.opening) throw new Error('recording_busy')
    this.opening = true
    try {
      const { normalizeRecordingFps } = await recordingOptions
      const frameRate = normalizeRecordingFps(fps)
      const capacity = await this.capacity(this.directory)
      const reason = recordingAdmission(capacity, automatic)
      if (reason) throw new Error(`recording_${reason}`)
      const now = new Date()
      const day = now.toISOString().slice(0, 10)
      const directory = path.join(this.directory, day)
      await fs.mkdir(directory, { recursive: true })
      const name = String(sessionId || 'session').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 64)
      const file = path.join(directory, `${now.toISOString().replace(/[:.]/g, '-')}_${name}_${randomUUID().slice(0, 8)}.webm`)
      const handle = await fs.open(file, 'wx', 0o600)
      this.active = { id: randomUUID(), file, handle, startedAt: now.toISOString(),
        sessionId: name, automatic, fps: frameRate, bytes: 0, writes: Promise.resolve(), closing: null }
      return { id: this.active.id, path: file, startedAt: this.active.startedAt, capacity, fps: frameRate }
    } finally { this.opening = false }
  }

  async append(id, data) {
    const job = this.active
    if (!job || job.id !== id || job.closing) throw new Error('recording_not_active')
    if (!(data instanceof ArrayBuffer) || data.byteLength > 32 * 1024 ** 2) {
      throw new Error('recording_invalid_chunk')
    }
    const buffer = Buffer.from(data)
    job.writes = job.writes.then(async () => {
      await job.handle.appendFile(buffer)
      job.bytes += buffer.length
    })
    await job.writes
    return job.bytes
  }

  async finish(id, reason = 'user') {
    const job = this.active
    if (!job || job.id !== id) throw new Error('recording_not_active')
    if (job.closing) return job.closing
    job.closing = (async () => {
      let warning = ''
      try { await job.writes } catch (error) { warning = error.message }
      try { await job.handle.sync() } catch (error) { warning = error.message }
      await job.handle.close()
      // Reindex without re-encoding so players can seek and show duration.
      const indexed = job.file.replace(/\.webm$/, '.indexed.webm')
      if (this.index && job.bytes > 0) {
        try {
          await promisify(execFile)('ffmpeg', ['-nostdin', '-y', '-v', 'error', '-i', job.file,
            '-map', '0:v:0', '-c', 'copy', indexed], { timeout: 120000 })
          await fs.chmod(indexed, 0o600)
          await fs.rename(indexed, job.file)
        } catch (error) {
          warning ||= `index: ${error.message}`
          await fs.rm(indexed, { force: true })
        }
      }
      const result = { path: job.file, startedAt: job.startedAt, endedAt: new Date().toISOString(),
        sessionId: job.sessionId, automatic: job.automatic, bytes: job.bytes, reason, warning,
        scope: 'application_window', audio: false, fps: job.fps }
      await fs.writeFile(`${job.file}.json`, JSON.stringify(result, null, 2), { mode: 0o600 })
      return result
    })().finally(() => { if (this.active === job) this.active = null })
    return job.closing
  }
}

module.exports = { RecordingFile, recordingCapacity, recordingAdmission }
