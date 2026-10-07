import { DEFAULT_RECORDING_FPS, normalizeRecordingFps } from '../../electron/recordingOptions.mjs'

export class ScreenRecorder {
  constructor(api, { onState = () => {}, mediaDevices = navigator.mediaDevices,
    Recorder = MediaRecorder } = {}) {
    this.api = api
    this.onState = onState
    this.mediaDevices = mediaDevices
    this.Recorder = Recorder
    this.job = null
    this.pending = 0
    this.busy = false
    this.generation = 0
  }

  async start(sessionId, automatic = false, frameRate = DEFAULT_RECORDING_FPS) {
    if (this.busy || this.job) return
    this.busy = true
    const generation = ++this.generation
    const fps = normalizeRecordingFps(frameRate)
    let job
    try {
      job = { ...await this.api.beginRecording({ sessionId, automatic, fps }), automatic: Boolean(automatic), fps }
      if (generation !== this.generation) {
        await this.api.finishRecording(job.id, 'start_cancelled')
        return
      }
      this.job = job
      this.writes = Promise.resolve()
      this.failure = ''
      this.stopping = null
      const stream = await this.mediaDevices.getUserMedia({ audio: false, video: { mandatory: {
        chromeMediaSource: 'desktop', chromeMediaSourceId: job.sourceId,
        maxWidth: 1920, maxHeight: 1080, maxFrameRate: fps,
      } } })
      if (generation !== this.generation || this.job !== job) {
        stream.getTracks().forEach(track => track.stop())
        return
      }
      this.stream = stream
      const mimeType = ['video/webm;codecs=vp8', 'video/webm'].find(type => this.Recorder.isTypeSupported(type))
      if (!mimeType) throw new Error('recording_codec')
      this.recorder = new this.Recorder(this.stream, { mimeType, videoBitsPerSecond: 4500000 })
      this.recorder.ondataavailable = event => {
        if (!event.data.size) return
        this.pending += event.data.size
        this.writes = this.writes.then(async () => {
          try { await this.api.appendRecording(job.id, await event.data.arrayBuffer()) }
          finally { this.pending -= event.data.size }
        }).catch(error => { this.failure ||= error.message; void this.stop('write_error') })
        if (this.pending > 32 * 1024 ** 2) {
          this.failure ||= 'recording_write_backlog'
          void this.stop('write_backlog')
        }
      }
      this.recorder.onerror = event => { this.failure = event.error?.message || 'recording_failed'; void this.stop('encoder_error') }
      this.stream.getVideoTracks()[0].onended = () => { void this.stop('capture_ended') }
      this.recorder.start(1000)
      this.onState({ state: 'recording', ...job })
    } catch (error) {
      this.stream?.getTracks().forEach(track => track.stop())
      if (job && this.job === job) await this.api.finishRecording(job.id, 'start_failed').catch(() => {})
      this.job = null
      if (generation === this.generation) this.onState({ state: 'error', error: error.message })
    } finally { this.busy = false }
  }

  async stop(reason = 'user') {
    if (this.stopping) return this.stopping
    ++this.generation
    if (!this.job) return
    this.stopping = (async () => {
      const job = this.job
      this.onState({ state: 'saving', ...job })
      try {
        if (this.recorder && this.recorder.state !== 'inactive') {
          await new Promise(resolve => {
            this.recorder.addEventListener('stop', resolve, { once: true })
            this.recorder.stop()
          })
        }
        await this.writes
        const result = await this.api.finishRecording(job.id, reason)
        this.onState({ state: this.failure ? 'error' : 'saved', ...result, error: this.failure })
      } catch (error) { this.onState({ state: 'error', path: job.path, error: error.message }) }
      finally {
        this.stream?.getTracks().forEach(track => track.stop())
        this.job = null
        this.recorder = null
        this.stopping = null
      }
    })()
    return this.stopping
  }
}
