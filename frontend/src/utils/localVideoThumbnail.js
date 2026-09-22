let captureQueue = Promise.resolve()

const waitForVideoEvent = (video, eventName, timeoutMs = 5000) => new Promise((resolve, reject) => {
  let timer = null

  const cleanup = () => {
    video.removeEventListener(eventName, onReady)
    video.removeEventListener('error', onError)
    if (timer) window.clearTimeout(timer)
  }
  const onReady = () => {
    cleanup()
    resolve()
  }
  const onError = () => {
    cleanup()
    reject(new Error(`Local video ${eventName} failed`))
  }

  video.addEventListener(eventName, onReady, { once: true })
  video.addEventListener('error', onError, { once: true })
  timer = window.setTimeout(() => {
    cleanup()
    reject(new Error(`Local video ${eventName} timed out`))
  }, timeoutMs)
})

const waitForDecodedFrame = async (video) => {
  if (typeof video.requestVideoFrameCallback !== 'function') return
  await new Promise((resolve) => {
    let settled = false
    const timer = window.setTimeout(() => {
      if (!settled) resolve()
      settled = true
    }, 700)
    video.requestVideoFrameCallback(() => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      resolve()
    })
  })
}

const captureFrame = async (source, timestamp, width, quality) => {
  const video = document.createElement('video')
  video.preload = 'auto'
  video.muted = true
  video.playsInline = true
  video.crossOrigin = 'anonymous'
  video.style.cssText = 'position:fixed;width:1px;height:1px;left:-10000px;top:-10000px;opacity:0;pointer-events:none'
  document.body.appendChild(video)

  try {
    const metadataReady = waitForVideoEvent(video, 'loadedmetadata')
    video.src = source
    video.load()
    await metadataReady

    const duration = Number.isFinite(video.duration) ? video.duration : Number(timestamp || 0) + 1
    const target = Math.min(Math.max(0, Number(timestamp || 0)), Math.max(0, duration - 0.04))
    if (target > 0.025) {
      const seeked = waitForVideoEvent(video, 'seeked')
      video.currentTime = target
      await seeked
    } else if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      await waitForVideoEvent(video, 'loadeddata')
    }
    await waitForDecodedFrame(video)

    const sourceWidth = video.videoWidth
    const sourceHeight = video.videoHeight
    if (!sourceWidth || !sourceHeight) throw new Error('Local video has no decoded frame')

    const outputWidth = Math.min(Math.max(160, Number(width || 360)), sourceWidth)
    const outputHeight = Math.max(1, Math.round(outputWidth * sourceHeight / sourceWidth))
    const canvas = document.createElement('canvas')
    canvas.width = outputWidth
    canvas.height = outputHeight
    const context = canvas.getContext('2d', { alpha: false })
    context.drawImage(video, 0, 0, outputWidth, outputHeight)
    return canvas.toDataURL('image/jpeg', Math.min(0.92, Math.max(0.5, Number(quality || 0.72))))
  } finally {
    video.pause()
    video.removeAttribute('src')
    video.load()
    video.remove()
  }
}

export const captureLocalVideoThumbnail = (source, timestamp, options = {}) => {
  if (!source) return Promise.reject(new Error('Local replay video URL is missing'))
  const job = captureQueue.then(() => captureFrame(
    source,
    timestamp,
    options.width,
    options.quality
  ))
  captureQueue = job.catch(() => {})
  return job
}
