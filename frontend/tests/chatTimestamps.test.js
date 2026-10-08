import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveReviewRange, tokenizeChatTimestamps, formatVideoTime } from '../src/utils/chatTimestamps.js'

const summaries = Array.from({ length: 800 }, (_, id) => ({ window_id: id, start_time: id * 5, end_time: id * 5 + 5 }))
const links = text => tokenizeChatTimestamps(text, summaries).filter(part => part.reference)

test('links point timestamps to their containing window, including zero', () => {
  assert.deepEqual(links('0:00，然后 01:02')[1].reference, { start_time: 60, end_time: 65, window_id: 12 })
  assert.equal(links('0:00')[0].reference.window_id, 0)
})

test('window boundary selects the next window; video EOF selects the last', () => {
  assert.equal(resolveReviewRange({ start_time: 5 }, summaries).window_id, 1)
  assert.equal(resolveReviewRange({ start_time: 4000 }, summaries).window_id, 799)
})

test('preserves ranges and supports hours, full-width colon, and Chinese separators', () => {
  assert.deepEqual(links('01:00–01:10')[0].reference, { start_time: 60, end_time: 70 })
  assert.equal(links('01:02:03')[0].reference.start_time, 3720)
  assert.equal(links('01：00 至 01：05')[0].reference.window_id, 12)
  assert.equal(links('01:00 to 01:05')[0].reference.end_time, 65)
})

test('invalid, reversed, future, URL and uncovered timestamps stay plain text', () => {
  for (const text of ['00:99', '01:10-01:00', '99:00', 'http://server:8001/x', 'https://host/?t=01:00']) {
    assert.equal(links(text).length, 0, text)
    assert.equal(tokenizeChatTimestamps(text, summaries).map(p => p.text).join(''), text)
  }
  assert.equal(resolveReviewRange({ start_time: 2, end_time: 12 }, [summaries[0], summaries[2]]), null)
  assert.equal(links('01:00-99:00').length, 0)
})

test('no records means no links; text and literal HTML remain intact', () => {
  const text = '<img src=x onerror=alert(1)> 00:05，00:10-00:15。'
  assert.equal(tokenizeChatTimestamps(text).filter(p => p.reference).length, 0)
  assert.equal(tokenizeChatTimestamps(text, summaries).map(p => p.text).join(''), text)
})

test('formats video time beyond one hour', () => {
  assert.equal(formatVideoTime(3723), '1:02:03')
  assert.equal(formatVideoTime(0), '0:00')
})

test('translated timestamps remain links beside Chinese punctuation and words', () => {
  assert.deepEqual(links('回看：0:05和0:15。').map(p => p.reference.window_id), [1, 3])
  assert.equal(links('记录：0:05-0:10：未见出血。')[0].reference.window_id, 1)
  assert.equal(links('Review:0:05')[0].reference.window_id, 1)
  assert.equal(links('https://host/?t=0:05').length, 0)
})
