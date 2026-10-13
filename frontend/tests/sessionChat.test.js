import test from 'node:test'
import assert from 'node:assert/strict'
import { createSessionChatStore, chatErrorText } from '../src/utils/sessionChat.js'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
const question = { text: 'What happened here?', reviewContext: { start_time: 5, end_time: 10, window_id: 1 } }
const answer = { data: { success: true, response: { content: 'See 0:05 and 0:15.' } } }
const history = [{ id: 1, role: 'user', content: 'Earlier question', created_at: '2026-10-07T10:00:00' }]
const emptyHistory = async () => ({ data: { messages: [] } })

test('main and every review panel share messages, draft and a single history request', async () => {
  let count = 0
  const store = createSessionChatStore({ api: { get: async () => { count++; return { data: { messages: history } } } } })
  const main = store.getSession('a')
  const review = store.getSession('a')
  assert.equal(main, review)
  await Promise.all([store.ensureHistory('a'), store.ensureHistory('a')])
  assert.equal(count, 1)
  main.draft = 'Follow-up'
  assert.equal(review.draft, 'Follow-up')
  assert.equal(review.messages[0].content, 'Earlier question')
  assert.equal(review.messages[0].time, '10:00:00')
  await store.ensureHistory('a')
  assert.equal(count, 1)
})

test('timestamp navigation or panel disposal cannot cancel a reply or change its captured clip', async () => {
  const pending = deferred()
  const posts = []
  const store = createSessionChatStore({ api: { get: emptyHistory, post: (url, payload) => { posts.push({ url, payload }); return pending.promise } } })
  const focus = { ...question.reviewContext }
  const sending = store.send('a', { ...question, reviewContext: focus })
  focus.start_time = 15
  focus.end_time = 20
  await tick()
  const reopened = store.getSession('a')
  assert.equal(reopened.isLoading, true)
  assert.deepEqual(posts[0].payload.review_context, question.reviewContext)
  assert.equal(await store.send('a', { text: 'Duplicate question' }), null)
  pending.resolve(answer)
  await sending
  assert.equal(posts.length, 1)
  assert.equal(reopened.messages.length, 2)
  assert.equal(reopened.messages[1].content, answer.data.response.content)
  assert.deepEqual(reopened.messages[1].reviewContext, question.reviewContext)
  assert.equal(reopened.isLoading, false)
})

test('late replies stay in their original video and unrelated videos keep separate drafts', async () => {
  const pending = deferred()
  const store = createSessionChatStore({ api: { get: emptyHistory, post: () => pending.promise } })
  const sending = store.send('a', question)
  await tick()
  store.getSession('b').draft = 'Another video'
  pending.resolve(answer)
  await sending
  assert.equal(store.getSession('a').messages.length, 2)
  assert.deepEqual(store.getSession('b').messages, [])
  assert.equal(store.getSession('b').draft, 'Another video')
  assert.equal(store.getSession('b').isLoading, false)
})

test('history loads before sending so hydration cannot erase a new exchange', async () => {
  const loading = deferred()
  let posts = 0
  const store = createSessionChatStore({ api: { get: () => loading.promise, post: async () => { posts++; return answer } } })
  const hydration = store.ensureHistory('a')
  const sending = store.send('a', question)
  await tick()
  assert.equal(posts, 0)
  loading.resolve({ data: { messages: history } })
  await Promise.all([hydration, sending])
  assert.equal(posts, 1)
  assert.deepEqual(store.getSession('a').messages.map(m => m.content), ['Earlier question', question.text, answer.data.response.content])
})

test('restores every history page in chronological order when an app is reopened', async () => {
  const cursors = []
  const store = createSessionChatStore({ api: { get: async (_url, { params }) => {
    cursors.push(params.before_id)
    return { data: params.before_id ? { messages: history, has_more: false } : { messages: [{ id: 2, role: 'assistant', content: 'Earlier reply' }], has_more: true } }
  } } })
  assert.equal(await store.ensureHistory('a'), true)
  assert.deepEqual(cursors, [undefined, 2])
  assert.deepEqual(store.getSession('a').messages.map(m => m.id), [1, 2])
})

test('history failures retain the draft and can be retried without duplicate questions', async () => {
  let attempts = 0, posts = 0
  const store = createSessionChatStore({ api: {
    get: async () => (++attempts === 1 ? { data: { error: 'Database unavailable' } } : { data: { messages: history } }),
    post: async () => { posts++; return answer },
  } })
  const state = store.getSession('a')
  state.draft = question.text
  await store.send('a', question)
  assert.equal(state.historyError, 'Database unavailable')
  assert.equal(state.draft, question.text)
  assert.equal(state.isLoading, false)
  assert.equal(posts, 0)
  await store.send('a', question)
  assert.equal(state.historyError, '')
  assert.equal(state.messages.length, 3)
  assert.equal(posts, 1)
})

test('a failed answer stays shared and releases the shared send lock', async () => {
  const store = createSessionChatStore({ api: { get: emptyHistory, post: async () => ({ data: { success: false, error: 'Model unavailable' } }) } })
  await store.send('a', question)
  const state = store.getSession('a')
  assert.equal(state.isLoading, false)
  assert.match(state.messages[1].content, /Model unavailable/)
  assert.equal(state.messages.length, 2)
})

test('main-view requests do not inherit the last review focus and search uses the captured session', async () => {
  const posts = []
  const store = createSessionChatStore({ api: { get: emptyHistory, post: async (url, payload) => {
    posts.push({ url, payload })
    return url.includes('/semantic') ? { data: { results: [{ window_id: 2, similarity: 0.9 }] } } : answer
  } } })
  await store.send('a', question)
  await store.send('a', { text: 'Find similar windows', hasSummaries: true })
  assert.equal(posts[1].payload.review_context, undefined)
  assert.equal(posts[2].payload.session_id, 'a')
  assert.equal(store.getSession('a').messages[3].similarWindows[0].window_id, 2)
})

test('a broken pagination cursor reports an error rather than looping forever', async () => {
  const store = createSessionChatStore({ api: { get: async () => ({ data: { messages: history, has_more: true } }) } })
  assert.equal(await store.ensureHistory('a'), false)
  assert.match(store.getSession('a').historyError, /cursor/)
})

test('structured API errors never render as object Object', () => {
  assert.equal(chatErrorText({ response: { data: { detail: [{ loc: ['body', 'content'], msg: 'Invalid' }] } } }), 'body.content: Invalid')
  assert.equal(chatErrorText({ response: { data: { detail: { message: 'Unavailable' } } } }), 'Unavailable')
})

test('structured failure bodies in successful HTTP responses remain readable', async () => {
  const store = createSessionChatStore({ api: {
    get: emptyHistory,
    post: async () => ({ data: { success: false, error: { message: 'Model unavailable' } } }),
  } })
  await store.send('a', question)
  assert.match(store.getSession('a').messages.at(-1).content, /Model unavailable/)
  assert.doesNotMatch(store.getSession('a').messages.at(-1).content, /\[object Object\]/)
  assert.equal(store.getSession('a').isLoading, false)
})

test('structured history failure retains the draft and can be retried', async () => {
  const store = createSessionChatStore({ api: {
    get: async () => ({ data: { error: [{ loc: ['session'], msg: 'Unavailable' }] } }),
  } })
  store.getSession('a').draft = 'Keep this question'
  assert.equal(await store.ensureHistory('a'), false)
  assert.equal(store.getSession('a').historyError, 'session: Unavailable')
  assert.equal(store.getSession('a').draft, 'Keep this question')
})
