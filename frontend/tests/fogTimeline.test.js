import test from 'node:test'
import assert from 'node:assert/strict'
import { summarizeFogTimeline } from '../src/utils/fogTimeline.js'

const fog = { fogActive: true }
const clear = { clearObserved: true }
const run = rows => summarizeFogTimeline(rows.map(([window_id, flags]) => ({ window_id, flags })), r => r.flags)

test('two adjacent explicit clear windows end an existing fog episode', () => {
  const result = run([[0, fog], [1, clear], [2, clear]])
  assert.equal(result.fogActive, false)
  assert.equal(result.latestResolved.window_id, 2)
  assert.deepEqual(result.windowIds, [0, 2])
})

test('missing, uncertain, or external views do not prove fog clearance', () => {
  for (const rows of [
    [[0, fog], [1, clear]],
    [[0, fog], [1, clear], [3, clear]],
    [[0, fog], [1, clear], [2, {}], [3, clear]],
    [[0, fog], [1, { outOfBody: true, clearObserved: true }]],
  ]) assert.equal(run(rows).fogActive, true)
})

test('clearance is not invented before fog, and subsequent fog can reopen', () => {
  assert.equal(run([[0, clear], [1, clear]]).latestResolved, null)
  const result = run([[0, fog], [1, clear], [2, clear], [3, fog]])
  assert.equal(result.fogActive, true)
  assert.equal(result.firstActive.window_id, 3)
  assert.equal(run([[0, fog], [1, { fogResolved: true }]]).fogActive, false)
})
