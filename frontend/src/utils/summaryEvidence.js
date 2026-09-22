const phaseOnly = /^当前处于(?:肝胆三角解剖|夹闭切断|胆囊分离|清洁凝血|胆囊取出与装袋|标本袋牵拉取出|准备阶段)[。；;]?$/
const cvsStatus = /CVS|安全关键视野|关键安全视野|critical view/i

export function preferDetailsOverPhase(text) {
  const sentences = String(text || '').split(/(?<=[。；;])/).map(s => s.trim()).filter(Boolean)
  const hasDetails = sentences.some(s => !phaseOnly.test(s) && !cvsStatus.test(s))
  return (hasDetails ? sentences.filter(s => !phaseOnly.test(s)) : sentences).join('')
}
