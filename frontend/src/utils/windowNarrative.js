const phases = {
  preparation: ['准备阶段', 'Preparation'],
  calottriangledissection: ['肝胆三角解剖', 'Hepatocystic triangle dissection'],
  clippingcutting: ['夹闭切断', 'Clipping and cutting'],
  gallbladderdissection: ['胆囊分离', 'Gallbladder dissection'],
  gallbladderpackaging: ['胆囊装袋', 'Gallbladder packaging'],
  gallbladderretraction: ['标本袋牵拉取出', 'Specimen retrieval'],
  cleaningcoagulation: ['清洁凝血', 'Cleaning and coagulation'],
}

export function phaseLabel(phase, language = 'zh') {
  const value = String(phase || '')
  const labels = phases[value.replace(/[_\s-]/g, '').toLowerCase()]
    || Object.values(phases).find(labels => labels.includes(value))
  return labels?.[language === 'en' ? 1 : 0] || (language === 'en' ? 'Phase unconfirmed' : '阶段待确认')
}

export function windowPhaseLabel(summary, language = 'zh') {
  return phaseLabel(summary?.phase || summary?.surgical_phase || summary?.dominant_phase
    || summary?.others?.experts?.phase?.label, language)
}

const phaseHeader = /^(?:当前处于|当前为)(?:肝胆三角解剖|夹闭切断|胆囊分离|清洁凝血|胆囊取出与装袋|胆囊装袋|标本袋牵拉取出|准备)(?:阶段)?[，,。；;]\s*/
const sentenceSegmenter = new Intl.Segmenter('en', { granularity: 'sentence' })
const sentences = text => [...sentenceSegmenter.segment(String(text || ''))]
  .flatMap(({ segment }) => segment.match(/[^。；！？!?;]+[。；！？!?;]?/g) || [])
  .map(s => s.trim()).filter(Boolean)
const fingerprint = text => text.replace(/\s+/g, '').replace(/[，,。；！？!?;.:]+$/g, '').toLowerCase()
const joinSentences = items => items.map((text, index) => {
  if (index === items.length - 1 || /[。；！？!?;.]$/.test(text)) return text
  return text + (/[\u3400-\u9fff]/.test(text) ? '。' : '.')
}).join('').replace(/([.!?])(?=[A-Za-z])/g, '$1 ')

export function stripCvsStatusClauses(text) {
  return String(text || '').replace(
    /(?:^|(?<=[。；;，,]))\s*(?:CVS|关键安全视野|安全关键视野|critical view(?: of safety)?)[^。；;，,.]*[。；;，,.]?/gi,
    clause => /(剪刀|剪断|切断|施夹|夹闭|出血|危险|风险|scissors|cutting|bleeding|risk)/i.test(clause) ? clause : '',
  ).trim()
}

// Display formatting must never infer anatomy, remove action subjects, or
// turn a negated/uncertain observation into an affirmative one.
export function formatWindowNarrative(text) {
  let result = stripCvsStatusClauses(text).replace(/^【[^】]*】\s*/, '')
  const withoutHeading = result.replace(phaseHeader, '')
  if (withoutHeading.trim()) result = withoutHeading
  const seen = new Set()
  return joinSentences(sentences(result).map(sentence => {
    // Remove only affirmative presentation boilerplate at the sentence start.
    // Never strip "not visible", "may be", or anatomical/operative qualifiers.
    let concise = sentence
      .replace(/^(?:in|within) (?:the )?(?:field of view|current (?:frame|view)|image|video|frame)[,:]?\s+/i, '')
      .replace(/^it (?:can be seen|is (?:noted|observed|seen)) that\s+/i, '')
      .replace(/^the (?:image|video|frame) shows that\s+/i, '')
    if (!/\b(?:no|not|neither|without|cannot|may|might|possible|possibly|uncertain|unclear|partly|partially|only)\b/i.test(concise)) {
      concise = concise.replace(/\b(is|are) (?:seen|observed) (?=[a-z]+ing\b)/gi, '$1 ')
        .replace(/\s+(?:is|are) (?:visible|seen|observed)(?: (?:in|within) the (?:field of view|image|frame))?(?=[.!?;]|$)/gi, '')
        .replace(/\s+(?:is|are) visible,\s+including\b/gi, ', including')
        .replace(/\s+(?:(?:is|are) (?:present )?)?(?:in|within) the field of view(?=[.!?;]|$)/gi, '')
    }
    return concise.replace(/^[a-z]/, letter => letter.toUpperCase())
  }).filter(sentence => {
    const key = fingerprint(sentence)
    if (/^(?:当前|目前|本窗口|本段)$/.test(key)) return false
    if (seen.has(key)) return false
    seen.add(key)
    return true
  }))
}

export function mergeWindowNarratives(windows, maxChars = 320) {
  const seen = new Set()
  const kept = []
  for (const window of windows) {
    for (const sentence of sentences(formatWindowNarrative(window.summary))) {
      const key = fingerprint(sentence)
      if (seen.has(key)) continue
      seen.add(key)
      kept.push(sentence)
    }
  }
  const text = joinSentences(kept)
  // Keep a separate chapter rather than discard a later operation or risk.
  return text.length <= maxChars ? text : null
}

export function isReviewedWindow(window) {
  return Boolean(window?.others?.manual_review || window?.others?.experts?.open_vlm?.success
    || window?.experts?.open_vlm?.success
    || Number(window?.stage) >= 2)
}
