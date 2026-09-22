import test from 'node:test'
import assert from 'node:assert/strict'
import { preferDetailsOverPhase } from '../src/utils/summaryEvidence.js'

test('phase heading does not displace the observed action in a one-sentence merge', () => {
  assert.equal(preferDetailsOverPhase('当前处于肝胆三角解剖。电凝钩分离组织。CVS评估中。'),
    '电凝钩分离组织。CVS评估中。')
})

test('phase-only fallback remains available', () => {
  assert.equal(preferDetailsOverPhase('当前处于肝胆三角解剖。CVS评估中。'),
    '当前处于肝胆三角解剖。CVS评估中。')
})

test('specific anatomy is never inferred from generic structure text', () => {
  assert.equal(preferDetailsOverPhase('当前处于夹闭切断。夹子夹闭管状结构。'), '夹子夹闭管状结构。')
})
