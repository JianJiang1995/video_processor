import test from 'node:test'
import assert from 'node:assert/strict'
import { countFiniteWindows } from '../src/utils/videoWindows.js'

test('container rounding cannot create a phantom EOF window', () => {
  assert.equal(countFiniteWindows(60.0058, 5, 50), 12)
  assert.equal(countFiniteWindows(100, 5, 30), 20)
  assert.equal(countFiniteWindows(100.01, 5, 30), 20)
  assert.equal(countFiniteWindows(8.001, 2, 60), 4)
})

test('a genuine partial final window is kept', () => {
  assert.equal(countFiniteWindows(60.04, 5, 50), 13)
  assert.equal(countFiniteWindows(100.3, 5, 30), 21)
  assert.equal(countFiniteWindows(1 / 60, 5, 60), 1)
  assert.equal(countFiniteWindows(60.0058, 5), 13)
})

test('unbounded streams and invalid metadata do not produce a finite count', () => {
  for (const duration of [0, -1, NaN, Infinity, undefined]) {
    assert.equal(countFiniteWindows(duration, 5, 50), 0)
  }
  assert.equal(countFiniteWindows(60, 0, 50), 0)
})
