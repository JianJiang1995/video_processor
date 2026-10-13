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

function recordingMediaInfo(probe) {
  const stream = probe.streams?.find(item => item.codec_type === 'video')
  if (!stream || stream.codec_name !== 'h264' ||
    !probe.format?.format_name?.split(',').includes('mp4') || !(stream.width > 0 && stream.height > 0)) {
    throw new Error('recording_invalid_mp4')
  }
  const [numerator, denominator] = String(stream.avg_frame_rate || '').split('/').map(Number)
  const fps = Number.isFinite(numerator) && Number.isFinite(denominator) && numerator > 0 && denominator > 0
    ? numerator / denominator : null
  const positive = value => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : null
  return { width: stream.width, height: stream.height, averageFps: fps,
    videoBitrate: positive(stream.bit_rate), durationSeconds: positive(probe.format.duration),
    codec: stream.codec_name, pixelFormat: stream.pix_fmt || null, container: 'mp4' }
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
      const file = path.join(directory, `${now.toISOString().replace(/[:.]/g, '-')}_${name}_${randomUUID().slice(0, 8)}.mp4`)
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
      const indexed = job.file.replace(/\.mp4$/, '.indexed.mp4')
      if (this.index && job.bytes > 0) {
        try {
          // Pre-create privately: FFmpeg must not expose frames while remuxing.
          const output = await fs.open(indexed, 'wx', 0o600)
          await output.close()
          await promisify(execFile)('ffmpeg', ['-nostdin', '-y', '-v', 'error', '-i', job.file,
            '-map', '0:v:0', '-c', 'copy', '-movflags', '+faststart', indexed], { timeout: 120000 })
          await fs.rename(indexed, job.file)
        } catch (error) {
          warning ||= `index: ${error.message}`
          await fs.rm(indexed, { force: true })
        }
      }
      const result = { path: job.file, startedAt: job.startedAt, endedAt: new Date().toISOString(),
        sessionId: job.sessionId, automatic: job.automatic, bytes: (await fs.stat(job.file)).size,
        inputBytes: job.bytes, reason, warning,
        scope: 'application_window', audio: false, fps: job.fps, format: 'mp4', codec: 'h264' }
      const { RECORDING_MAX_WIDTH, RECORDING_MAX_HEIGHT, RECORDING_BITRATE } = await recordingOptions
      result.requestedEncoding = { maxWidth: RECORDING_MAX_WIDTH, maxHeight: RECORDING_MAX_HEIGHT,
        fps: job.fps, videoBitrate: RECORDING_BITRATE, lossless: false }
      result.media = null
      if (result.bytes === 0) result.warning = 'recording_empty'
      if (this.index && result.bytes > 0) {
        try {
          const { stdout } = await promisify(execFile)('ffprobe', ['-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'format=format_name,duration:stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,bit_rate',
            '-of', 'json', job.file], { timeout: 15000 })
          result.media = recordingMediaInfo(JSON.parse(stdout))
        } catch (error) { result.warning ||= `validation: ${error.message}` }
      }
      await fs.writeFile(`${job.file}.json`, JSON.stringify(result, null, 2), { mode: 0o600 })
      return result
    })().finally(() => { if (this.active === job) this.active = null })
    return job.closing
  }
}

module.exports = { RecordingFile, recordingCapacity, recordingAdmission, recordingMediaInfo }
