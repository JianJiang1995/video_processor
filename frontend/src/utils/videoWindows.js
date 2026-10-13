export function countFiniteWindows(duration, windowDuration, sourceFps = 0) {
  const seconds = Number(duration)
  const windowSeconds = Number(windowDuration)
  const fps = Number(sourceFps)
  if (!Number.isFinite(seconds) || seconds <= 0 ||
      !Number.isFinite(windowSeconds) || windowSeconds <= 0) return 0
  // Container rounding shorter than half a source frame is not another window.
  const rounding = Number.isFinite(fps) && fps > 0 ? .5 / fps : 0
  return Math.max(1, Math.ceil((seconds - rounding) / windowSeconds))
}
