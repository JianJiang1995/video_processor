import { parsePreviewPacket } from './previewPixels.js'

let canvas, gl, textures, planarLocation, socket, retryTimer, animationFrame, pendingFrame
let url = '', paused = true, failed = false, generation = 0
let connectFailures = 0
let width = 0, height = 0, sourceFps = 0, firstFrame = true
let pixelFormat = ''
let displayRefreshRate = 0
let intervalStart = performance.now(), lastFrame = 0, frames = 0, maxGap = 0, latencies = []
let transportMs = 0, uploadMs = 0, received = 0, refreshTicks = 0

function resetMetrics() {
  intervalStart = performance.now()
  lastFrame = 0
  frames = 0
  maxGap = 0
  latencies = []
  transportMs = uploadMs = received = refreshTicks = 0
}

function disconnect() {
  generation++
  clearTimeout(retryTimer)
  cancelAnimationFrame(animationFrame)
  pendingFrame = null
  if (socket) {
    socket.onclose = socket.onmessage = socket.onerror = null
    socket.close()
    socket = null
  }
}

function fail(error) {
  failed = true
  disconnect()
  self.postMessage({ type: 'error', error: String(error?.message || error) })
}

function shader(type, source) {
  const result = gl.createShader(type)
  gl.shaderSource(result, source)
  gl.compileShader(result)
  if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(result))
  return result
}

function initialize(target) {
  canvas = target
  gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false, preserveDrawingBuffer: false })
  if (!gl) throw new Error('WebGL2 unavailable')
  canvas.addEventListener('webglcontextlost', () => fail(new Error('Preview GPU context lost')))
  const vertex = shader(gl.VERTEX_SHADER, `#version 300 es
    in vec2 position;
    out vec2 uv;
    void main() { gl_Position = vec4(position, 0.0, 1.0); uv = vec2(position.x * 0.5 + 0.5, 0.5 - position.y * 0.5); }`)
  const fragment = shader(gl.FRAGMENT_SHADER, `#version 300 es
    precision highp float;
    uniform sampler2D pixels;
    uniform sampler2D chromaU;
    uniform sampler2D chromaV;
    uniform bool planar;
    in vec2 uv;
    out vec4 color;
    void main() {
      if (planar) {
        float y = (texture(pixels, uv).r - 16.0/255.0) * (255.0/219.0);
        float u = texture(chromaU, uv).r - 128.0/255.0;
        float v = texture(chromaV, uv).r - 128.0/255.0;
        color = vec4(y + 1.596027*v, y - 0.391762*u - 0.812968*v, y + 2.017232*u, 1.0);
      } else { color = vec4(texture(pixels, uv).bgr, 1.0); }
    }`)
  const program = gl.createProgram()
  gl.attachShader(program, vertex)
  gl.attachShader(program, fragment)
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program))
  gl.deleteShader(vertex)
  gl.deleteShader(fragment)
  gl.useProgram(program)
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  const location = gl.getAttribLocation(program, 'position')
  gl.enableVertexAttribArray(location)
  gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0)
  textures = ['pixels', 'chromaU', 'chromaV'].map((name, index) => {
    gl.activeTexture(gl.TEXTURE0 + index)
    const texture = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, 1, 1, 0, gl.RED, gl.UNSIGNED_BYTE, null)
    gl.uniform1i(gl.getUniformLocation(program, name), index)
    return texture
  })
  planarLocation = gl.getUniformLocation(program, 'planar')
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
}

function completeFrame(frame, token) {
  if (token !== generation || paused) return
  const now = performance.now()
  frames++
  if (lastFrame) maxGap = Math.max(maxGap, now - lastFrame)
  lastFrame = now
  latencies.push(Math.max(0, Date.now() - frame.readAt))
  transportMs += frame.transportMs
  uploadMs += frame.uploadMs
  if (firstFrame) {
    self.postMessage({ type: 'frame' })
    firstFrame = false
  }
  if (now - intervalStart >= 2000) {
    latencies.sort((a, b) => a - b)
    self.postMessage({ type: 'metrics', metrics: {
      transport: `${pixelFormat}-webgl2`, width, height, sourceFps, frames,
      fps: frames * 1000 / (now - intervalStart), maxGapMs: maxGap,
      p95PipelineMs: latencies[Math.min(latencies.length - 1, Math.floor(latencies.length * 0.95))],
      meanTransportMs: transportMs / frames, meanUploadMs: uploadMs / frames,
      receivedFps: received * 1000 / (now - intervalStart),
      refreshFps: refreshTicks * 1000 / (now - intervalStart),
      displayRefreshRate,
      measuredAt: Date.now(), sequence: frame.sequence, paused: false,
    } })
    intervalStart = now
    frames = 0
    maxGap = 0
    latencies = []
    transportMs = uploadMs = received = refreshTicks = 0
  }
}

function draw(frame, token) {
  if (token !== generation || paused) return
  const receivedAt = performance.now()
  sourceFps = frame.sourceFps
  const resize = width !== frame.width || height !== frame.height || pixelFormat !== frame.format
  if (resize) {
    width = canvas.width = frame.width
    height = canvas.height = frame.height
    gl.viewport(0, 0, width, height)
    pixelFormat = frame.format
  }
  const planar = pixelFormat === 'i420'
  gl.uniform1i(planarLocation, planar ? 1 : 0)
  let offset = 0
  for (let index = 0; index < (planar ? 3 : 1); index++) {
    const w = index ? width / 2 : width
    const h = index ? height / 2 : height
    const channels = planar ? 1 : 3
    gl.activeTexture(gl.TEXTURE0 + index)
    gl.bindTexture(gl.TEXTURE_2D, textures[index])
    const format = planar ? gl.RED : gl.RGB
    if (resize) gl.texImage2D(gl.TEXTURE_2D, 0, planar ? gl.R8 : gl.RGB8, w, h, 0, format, gl.UNSIGNED_BYTE, null)
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, w, h, format, gl.UNSIGNED_BYTE, frame.pixels.subarray(offset, offset + w * h * channels))
    offset += w * h * channels
  }
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  gl.flush()
  frame.uploadedAt = performance.now()
  frame.uploadMs = frame.uploadedAt - receivedAt
  completeFrame(frame, token)
}

function renderLatest(token) {
  if (token !== generation || paused || failed) return
  refreshTicks++
  const frame = pendingFrame
  pendingFrame = null
  try { if (frame) draw(frame, token) }
  catch (error) { fail(error); return }
  animationFrame = requestAnimationFrame(() => renderLatest(token))
}

function connect() {
  disconnect()
  if (paused || failed || !url) return
  resetMetrics()
  firstFrame = true
  const token = generation
  animationFrame = requestAnimationFrame(() => renderLatest(token))
  socket = new WebSocket(url)
  const activeSocket = socket
  activeSocket.binaryType = 'arraybuffer'
  activeSocket.onmessage = event => {
    if (token !== generation || paused) return
    try {
      const frame = parsePreviewPacket(event.data)
      connectFailures = 0
      frame.transportMs = Date.now() - frame.readAt
      // A single latest slot decouples input cadence from monitor refresh.
      // Never queue stale frames or synchronously wait for a GPU fence.
      pendingFrame = frame
      received++
      activeSocket.send('ready')
    }
    catch (error) { fail(error) }
  }
  activeSocket.onerror = () => { /* onclose handles transient reconnects */ }
  activeSocket.onclose = event => {
    if (token !== generation || paused || failed) return
    if (event.code === 1008) return fail(new Error(event.reason || 'Raw preview unavailable'))
    if (++connectFailures >= 4) return fail(new Error('Local preview disconnected'))
    retryTimer = setTimeout(connect, 500)
  }
}

self.onmessage = ({ data }) => {
  try {
    if (data.type === 'init') {
      initialize(data.canvas)
      url = data.url
      paused = data.paused
      connect()
    } else if (data.type === 'display') {
      displayRefreshRate = Math.max(0, Number(data.refreshRate) || 0)
    } else if (data.type === 'pause') {
      paused = data.paused
      if (paused) {
        disconnect()
        self.postMessage({ type: 'metrics', metrics: { paused: true, measuredAt: Date.now() } })
      } else connect()
    }
  } catch (error) { fail(error) }
}
