export type StaminaReading = {
  stamina_current?: number | null
  stamina_max?: number | null
  stamina_recovery_per_5m?: number | null
  stamina_observed_at?: string | null
  role_online: boolean
  received_at?: string | null
}

/** Estimate whole five-minute recoveries since the last confirmed reading. */
export function estimateStamina(reading: StaminaReading, nowMs: number): number | null {
  const current = reading.stamina_current
  const maximum = reading.stamina_max
  const recovery = reading.stamina_recovery_per_5m
  const observedAt = Date.parse(reading.stamina_observed_at || '')
  if (current == null || maximum == null || recovery == null ||
      !Number.isFinite(current) || !Number.isFinite(maximum) || !Number.isFinite(recovery) ||
      !Number.isFinite(observedAt) || !Number.isFinite(nowMs) || maximum <= 0 || recovery <= 0) return null

  const lastSeen = Date.parse(reading.received_at || '')
  const estimateUntil = reading.role_online ? nowMs : Number.isFinite(lastSeen) ? Math.min(nowMs, lastSeen) : observedAt
  const elapsed = Math.max(0, estimateUntil - observedAt)
  const recoveries = Math.floor(elapsed / (5 * 60 * 1000))
  return Math.min(maximum, Math.max(0, current + recoveries * recovery))
}
