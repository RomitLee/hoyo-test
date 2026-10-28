import assert from 'node:assert/strict'
import test from 'node:test'
import { compositionUnits, dailyProfit, synthesisRows } from '../src/gemMath.ts'

test('all three composition rules match source calculator', () => {
  assert.equal(compositionUnits('normal', 8), 128)
  assert.equal(compositionUnits('star', 5), 81)
  assert.equal(compositionUnits('dust', 2), 2)
  assert.equal(compositionUnits('dust', 3), 5)
  assert.equal(compositionUnits('dust', 4), 12)
})

test('synthesis uses the saved ratio and selected server quote', () => {
  const ratios = Object.fromEntries(Array.from({ length: 11 }, (_, index) => [String(index + 2), 1.2]))
  const rows = synthesisRows('normal', 100000, 127000, ratios, 210, 'ratio')
  assert.equal(rows[0].units, 2)
  assert.equal(rows[0].costCoins, 200000)
  assert.equal(rows[0].stallWan, 24)
  assert.equal(rows[0].cbgPrice, null)
  assert.equal(rows[6].cbgPrice, 128 * 100000 * 1.2 / 30000000 * 210)
  assert.equal(rows[6].cbgAfterFee, rows[6].cbgPrice * .95)
  assert.equal(synthesisRows('normal', 100000, 127000, ratios, 210, 'average')[0].stallWan, 25)
})

test('single-server net profit and break-even retain source units', () => {
  const result = dailyProfit(9000, 17, 3, 222, 30)
  assert.equal(result.grossWan, 1530)
  assert.ok(Math.abs(result.cardYuan - 43.2) < 1e-9)
  assert.ok(Math.abs(result.cardWan - 43.2 * 3000 / 222) < 1e-9)
  assert.equal(result.netWan, 1530 - result.cardWan)
  assert.ok(result.breakEvenWan > 0 && result.targetPurchaseWan > result.breakEvenWan)
})

test('overall daily profit deducts point cards and converts net coins to RMB', () => {
  const result = dailyProfit(10000, 20, 10, 240, 0)
  assert.equal(result.grossWan, 2000)
  assert.ok(Math.abs(result.cardYuan - 144) < 1e-9)
  assert.ok(Math.abs(result.cardWan - 1800) < 1e-9)
  assert.ok(Math.abs(result.netWan - 200) < 1e-9)
  assert.ok(Math.abs(result.netWan / 3000 * 240 - 16) < 1e-9)
})
