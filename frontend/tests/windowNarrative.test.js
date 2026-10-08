import test from 'node:test'
import assert from 'node:assert/strict'
import { formatWindowNarrative, mergeWindowNarratives, phaseLabel, windowPhaseLabel, isReviewedWindow, stripCvsStatusClauses } from '../src/utils/windowNarrative.js'

test('phase labels cover stored aliases without leaking English into Chinese', () => {
  assert.equal(phaseLabel('calot_triangle_dissection'), '肝胆三角解剖')
  assert.equal(phaseLabel('ClippingCutting', 'en'), 'Clipping and cutting')
  assert.equal(phaseLabel('Unknown'), '阶段待确认')
  assert.equal(windowPhaseLabel({ surgical_phase: 'Preparation' }), '准备阶段')
  assert.equal(windowPhaseLabel({ phase: 'Unknown', others: { experts: { phase: { label: 'ClippingCutting' } } } }), '阶段待确认')
})

test('display preserves instruments, actions, uncertainty, and generic targets', () => {
  for (const text of [
    '抓钳牵拉胆囊，电凝钩分离胆囊与肝床之间的组织。',
    '可见剪刀接近组织，尚不能确认剪断。',
    '多枚金属钛夹闭合的管状结构残端。',
    '施夹器夹闭管状结构。未见明确活动性出血。',
    '冲吸器吸引术野液体。',
    '肝脏及下方胆囊显露，当前未见器械操作。',
  ]) assert.equal(formatWindowNarrative(text), text)
  assert.equal(formatWindowNarrative('当前处于肝胆三角解剖。抓钳牵拉胆囊。'), '抓钳牵拉胆囊。')
  assert.equal(formatWindowNarrative('当前；可见肝脏。'), '可见肝脏。')
})

test('deduplication does not confuse different targets or later cutting/bleeding', () => {
  assert.equal(mergeWindowNarratives([
    { summary: '施夹器正在夹闭胆囊管。' },
    { summary: '施夹器正在夹闭胆囊管。施夹器正在夹闭胆囊动脉。' },
    { summary: '剪刀剪断胆囊管。术野有活动性出血。' },
  ]), '施夹器正在夹闭胆囊管。施夹器正在夹闭胆囊动脉。剪刀剪断胆囊管。术野有活动性出血。')
  assert.equal(mergeWindowNarratives([{ summary: '抓钳牵拉胆囊。' }, { summary: '剪刀剪断胆囊管。' }], 10), null)
  assert.equal(mergeWindowNarratives([{ summary: '抓钳牵拉胆囊' }, { summary: '剪刀剪断胆囊管' }]), '抓钳牵拉胆囊。剪刀剪断胆囊管')
  assert.equal(mergeWindowNarratives([{ summary: '移动约1.5毫米。' }, { summary: '移动约15毫米。' }]), '移动约1.5毫米。移动约15毫米。')
})

test('pending expert snapshots do not count as finalized review', () => {
  assert.equal(isReviewedWindow({ stage: 1 }), false)
  assert.equal(isReviewedWindow({ stage: 1, others: { experts: { open_vlm: { pending: true } } } }), false)
  assert.equal(isReviewedWindow({ stage: 1, others: { experts: { open_vlm: { success: true } } } }), true)
  assert.equal(isReviewedWindow({ stage: 1, experts: { open_vlm: { success: true } } }), true)
})

test('CVS status stays separate without removing an adjacent operation or risk', () => {
  assert.equal(stripCvsStatusClauses('CVS安全视野确认中。抓钳牵拉胆囊。'), '抓钳牵拉胆囊。')
  assert.equal(stripCvsStatusClauses('CVS仍在评估中，抓钳牵拉胆囊。'), '抓钳牵拉胆囊。')
  assert.equal(stripCvsStatusClauses('CVS尚未达成时剪刀接近组织。'), 'CVS尚未达成时剪刀接近组织。')
  assert.equal(mergeWindowNarratives([{ summary: 'A grasper holds tissue.' }, { summary: 'Scissors approach tissue.' }]),
    'A grasper holds tissue. Scissors approach tissue.')
})

test('English narration removes camera boilerplate without changing the clinical finding', () => {
  assert.equal(formatWindowNarrative('In the field of view, the grasper retracts the gallbladder. It can be seen that the hook dissects adhesions.'),
    'The grasper retracts the gallbladder. The hook dissects adhesions.')
  assert.equal(formatWindowNarrative('Within the current frame, no definite division is identified.'),
    'No definite division is identified.')
  assert.equal(formatWindowNarrative('The grasper is seen retracting the gallbladder.'),
    'The grasper is retracting the gallbladder.')
  assert.equal(formatWindowNarrative('Liver, omentum and gallbladder surface tissue are visible in the field of view.'),
    'Liver, omentum and gallbladder surface tissue.')
  assert.equal(formatWindowNarrative('Tissue within the abdominal cavity is visible, including liver and omentum.'),
    'Tissue within the abdominal cavity, including liver and omentum.')
  assert.equal(formatWindowNarrative('Lens fogging is present in the field of view. The surgical field is obstructed.'),
    'Lens fogging. The surgical field is obstructed.')
  assert.equal(formatWindowNarrative('Blurry pink intra-abdominal tissue in the field of view.'),
    'Blurry pink intra-abdominal tissue.')
  for (const text of [
    'The cystic artery is not visible.',
    'A grasper may be approaching the cystic duct.',
    'It cannot be confirmed that division has occurred.',
    'No scissors are seen approaching the duct.',
    'The field of view is obscured by fog.',
    'The grasper holds tissue in the hepatocystic triangle.',
    'The instrument is partially in the field of view.',
    'The hook moves 1.5 mm. No active bleeding is seen.',
  ]) assert.equal(formatWindowNarrative(text), text)
})

test('English sentence deduplication preserves separate operations and risks', () => {
  assert.equal(mergeWindowNarratives([
    { summary: 'The grasper retracts the gallbladder. The hook dissects adhesions.' },
    { summary: 'The hook dissects adhesions. Active bleeding is present.' },
  ]), 'The grasper retracts the gallbladder. The hook dissects adhesions. Active bleeding is present.')
})
