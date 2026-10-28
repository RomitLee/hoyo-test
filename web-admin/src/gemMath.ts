export type GemKind = 'normal' | 'star' | 'dust'
export type SaleMode = 'ratio' | 'average'

export const gemTypes: Record<GemKind, { name: string; levels: number[]; cbgMinLevel: number; rule: string }> = {
  normal: { name: '普通宝石', levels: Array.from({ length: 11 }, (_, i) => i + 2), cbgMinLevel: 8, rule: '2 个低一级宝石合成 1 个高一级宝石' },
  star: { name: '星辉石', levels: Array.from({ length: 7 }, (_, i) => i + 2), cbgMinLevel: 5, rule: '3 个低一级星辉石合成 1 个高一级星辉石' },
  dust: { name: '五色灵尘', levels: Array.from({ length: 7 }, (_, i) => i + 2), cbgMinLevel: 5, rule: '2 个低一级灵尘加 1 个再低一级灵尘合成 1 个高一级灵尘' },
}

export function compositionUnits(kind: GemKind, level: number): number {
  if (kind === 'normal') return 2 ** (level - 1)
  if (kind === 'star') return 3 ** (level - 1)
  const units = [0, 1, 2]
  for (let current = 3; current <= level; current++) units[current] = 2 * units[current - 1] + units[current - 2]
  return units[level]
}

export function synthesisRows(kind: GemKind, basePrice: number, averagePrice: number,
  ratios: Record<string, number>, quote: number, mode: SaleMode) {
  return gemTypes[kind].levels.map(level => {
    const units = compositionUnits(kind, level)
    const costCoins = basePrice * units
    const saleCoins = mode === 'average' ? averagePrice * units : costCoins * ratios[String(level)]
    const cbgPrice = saleCoins / 30_000_000 * quote
    return {
      level, units, costCoins, costYuan: costCoins / 30_000_000 * quote,
      marginPercent: costCoins > 0 ? (saleCoins / costCoins - 1) * 100 : null,
      stallWan: Math.floor(Math.round(saleCoins / 10_000 * 1_000_000) / 1_000_000),
      cbgPrice: level >= gemTypes[kind].cbgMinLevel ? cbgPrice : null,
      cbgAfterFee: level >= gemTypes[kind].cbgMinLevel ? cbgPrice * .95 : null,
    }
  })
}

export function dailyProfit(purchaseWan: number, marginPercent: number, accountCount: number,
  quoteYuan: number, targetYuan: number) {
  const margin = marginPercent / 100
  const cardYuan = accountCount * .6 * 24
  const cardWan = quoteYuan > 0 ? cardYuan * 3000 / quoteYuan : 0
  const grossWan = purchaseWan * margin
  const rmbPerPurchaseWan = margin / 3000 * quoteYuan
  const breakEvenWan = rmbPerPurchaseWan > 0 ? cardYuan / rmbPerPurchaseWan : 0
  return {
    salesWan: purchaseWan * (1 + margin), grossWan,
    cbgYuan: grossWan / 3000 * quoteYuan, cardYuan, cardWan,
    netWan: grossWan - cardWan, breakEvenWan,
    gapWan: purchaseWan - breakEvenWan,
    targetPurchaseWan: rmbPerPurchaseWan > 0 ? (cardYuan + targetYuan) / rmbPerPurchaseWan : 0,
  }
}
