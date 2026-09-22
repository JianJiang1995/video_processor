// Select once before mounting the video. Never replace a playing decoder's URL.
export async function selectPlaybackPreview(fetchInfo, { signal, timeoutMs = 1500, pollMs = 100 } = {}) {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) abort()
  const timer = setTimeout(abort, timeoutMs)
  try {
    while (!controller.signal.aborted) {
      const info = await fetchInfo(controller.signal)
      if (controller.signal.aborted) return false
      if (info.status === 'ready') return true
      if (info.status !== 'preparing') return false
      await new Promise(resolve => setTimeout(resolve, pollMs))
    }
  } catch (_) {
    // A cold cache or unavailable backend must not prevent original playback.
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
    controller.abort()
  }
  return false
}
