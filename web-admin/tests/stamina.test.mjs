import assert from 'node:assert/strict'
import test from 'node:test'
import { estimateStamina } from '../src/stamina.ts'

const reading = {
  stamina_current: 314,
  stamina_max: 863,
  stamina_recovery_per_5m: 12,
  stamina_observed_at: '2026-10-27T10:57:42+08:00',
  role_online: true,
}
const observed = Date.parse(reading.stamina_observed_at)

test('stamina increases only at whole five-minute ticks and stops at the maximum', () => {
  assert.equal(estimateStamina(reading, observed), 314)
  assert.equal(estimateStamina(reading, observed + 4 * 60_000), 314)
  assert.equal(estimateStamina(reading, observed + 5 * 60_000), 326)
  assert.equal(estimateStamina(reading, observed + 10 * 60_000), 338)
  assert.equal(estimateStamina(reading, observed + 230 * 60_000), 863)
  assert.equal(estimateStamina(reading, observed + 24 * 60 * 60_000), 863)
})

test('offline role does not gain estimated stamina after its last report', () => {
  const offline = { ...reading, role_online: false, received_at: '2026-10-27T11:09:42+08:00' }
  assert.equal(estimateStamina(offline, observed + 60 * 60_000), 338)
})

test('missing or invalid observation does not invent a value', () => {
  assert.equal(estimateStamina({ ...reading, stamina_observed_at: null }, observed), null)
  assert.equal(estimateStamina({ ...reading, stamina_recovery_per_5m: null }, observed), null)
})
