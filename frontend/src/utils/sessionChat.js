import { reactive } from 'vue'
import axios from 'axios'

function errorDetailText(detail) {
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (typeof item === 'string') return item
      const loc = Array.isArray(item?.loc) ? item.loc.join('.') : ''
      const msg = errorDetailText(item)
      return loc ? `${loc}: ${msg}` : msg
    }).join('; ')
  }
  if (detail && typeof detail === 'object') {
    return errorDetailText(detail.message || detail.msg || detail.error || detail.detail || JSON.stringify(detail))
  }
  return String(detail ?? '')
}

export function chatErrorText(error) {
  return errorDetailText(error?.response?.data?.detail ?? error?.response?.data?.error ??
    error?.response?.data ?? error?.message ?? '')
}

function messageTime(value) {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return ''
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map(n => String(n).padStart(2, '0')).join(':')
}

// Requests belong to the video session, not to a mounted panel or review range.
export function createSessionChatStore({ api = axios, now = Date.now } = {}) {
  const sessions = new Map()
  const historyRequests = new Map()
  const getSession = sessionId => {
    if (!sessions.has(sessionId)) {
      sessions.set(sessionId, reactive({
        messages: [], draft: '', isLoading: false, isHydrating: false,
        historyLoaded: false, historyError: '',
      }))
    }
    return sessions.get(sessionId)
  }
  const endpoint = sessionId => `/api/voice/chat/${encodeURIComponent(sessionId)}`

  function ensureHistory(sessionId) {
    const state = getSession(sessionId)
    if (!sessionId || state.historyLoaded) return Promise.resolve(true)
    if (historyRequests.has(sessionId)) return historyRequests.get(sessionId)
    state.isHydrating = true
    state.historyError = ''
    const request = (async () => {
      try {
        let beforeId
        let messages = []
        do {
          const { data } = await api.get(`${endpoint(sessionId)}/history`, {
            params: { limit: 200, ...(beforeId ? { before_id: beforeId } : {}) }, timeout: 15000,
          })
          if (data.error) throw new Error(chatErrorText({ response: { data } }))
          if (!Array.isArray(data.messages)) throw new Error('Invalid chat history response')
          messages = [...data.messages, ...messages]
          if (!data.has_more) break
          const next = Number(data.messages[0]?.id)
          if (!Number.isFinite(next) || next <= 0 || (beforeId && next >= beforeId)) {
            throw new Error('Invalid chat history cursor')
          }
          beforeId = next
        } while (true)
        // Sending waits for hydration so a late history response cannot erase a reply.
        state.messages = messages.map(msg => ({ ...msg, time: messageTime(msg.created_at) }))
        state.historyLoaded = true
        return true
      } catch (error) {
        state.historyError = chatErrorText(error)
        return false
      } finally {
        state.isHydrating = false
        historyRequests.delete(sessionId)
      }
    })()
    historyRequests.set(sessionId, request)
    return request
  }

  async function send(sessionId, { text, reviewContext = null, hasSummaries = false, t = key => key }) {
    text = String(text || '').trim()
    const state = getSession(sessionId)
    if (!sessionId || !text || state.isLoading) return null
    const focus = reviewContext ? { ...reviewContext } : null
    const context = focus ? t('review.contextUsed') : hasSummaries ? t('chat.contextUsed') : null
    state.isLoading = true
    try {
      if (!await ensureHistory(sessionId)) return null
      state.messages.push({ role: 'user', content: text, time: messageTime(now()), reviewContext: focus })
      state.draft = ''
      const { data } = await api.post(`${endpoint(sessionId)}/send`, {
        role: 'user', content: text, timestamp: now() / 1000,
        ...(focus ? { review_context: focus } : {}),
      }, { timeout: 180000 })
      if (data.success === false) throw new Error(data.error ? chatErrorText({ response: { data } }) : t('chat.noResponse'))
      const reply = {
        role: 'assistant', content: data.response?.content || data.response_text || t('chat.noResponse'),
        time: messageTime(now()), context, reviewContext: focus, similarWindows: null,
      }
      state.messages.push(reply)
      if (/similar|相似|find.*like|像.*窗口/i.test(text)) {
        try {
          const result = await api.post('/api/analysis/search/semantic', {
            session_id: sessionId, query: text, top_k: 5,
          }, { timeout: 15000 })
          // Mutate through the reactive array so every open panel updates.
          state.messages[state.messages.length - 1].similarWindows = result.data?.results || null
        } catch (error) {
          console.warn('[Chat] Semantic search failed:', chatErrorText(error))
        }
      }
      return { reply, audio: data.response?.audio_base64 }
    } catch (error) {
      state.messages.push({ role: 'assistant', content: `${t('chat.errorPrefix')}: ${chatErrorText(error) || t('chat.networkError')}`, time: messageTime(now()) })
      return null
    } finally {
      state.isLoading = false
    }
  }

  return { getSession, ensureHistory, send }
}

export const sessionChat = createSessionChatStore()
