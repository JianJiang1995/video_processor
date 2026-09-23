export function summarizeFogTimeline(summaries, flagsFor) {
  let fogActive = false
  let firstActive = null
  let latestActive = null
  let latestResolved = null
  let previousClearId = null
  let clearStreak = 0
  const windowIds = []
  for (const summary of [...summaries].sort((a, b) => Number(a.window_id) - Number(b.window_id))) {
    const flags = flagsFor(summary)
    const wid = Number(summary.window_id)
    if (flags.fogActive) {
      if (!fogActive) firstActive = summary
      fogActive = true
      latestActive = summary
      windowIds.push(wid)
    }
    if (flags.clearObserved && !flags.fogActive && !flags.outOfBody) {
      clearStreak = previousClearId === wid - 1 ? clearStreak + 1 : 1
      previousClearId = wid
    } else {
      clearStreak = 0
      previousClearId = null
    }
    // A missing fog observation is not clearance; require explicit clear views.
    if (fogActive && (flags.fogResolved || clearStreak >= 2)) {
      latestResolved = summary
      fogActive = false
      windowIds.push(wid)
    }
  }
  return { fogActive, firstActive, latestActive, latestResolved, windowIds }
}
