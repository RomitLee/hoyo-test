<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, time } from '../api'
import Panel from './Panel.vue'
import blackGem from '../assets/market-gems/black-gem.png'
import fiveColorDust from '../assets/market-gems/five-color-dust.png'
import lightGem from '../assets/market-gems/light-gem.png'
import moonstone from '../assets/market-gems/moonstone.png'
import redAgate from '../assets/market-gems/red-agate.png'
import relic from '../assets/market-gems/relic.png'
import starGem from '../assets/market-gems/star-gem.png'
import sunstone from '../assets/market-gems/sunstone.png'

type Quote = { gem_name: string; price_coins: number; observed_at?: string }
type Stall = { server_name: string; character_id: string; role_name?: string | null; latest_at?: string; quotes: Quote[] }
type Merchant = { server_name: string; stall_owner_id: string; latest_at?: string; quotes: Quote[] }
const stalls = ref<Stall[]>([])
const merchants = ref<Merchant[]>([])
const loading = ref(true)
const error = ref('')
const refreshedAt = ref('')
const removing = ref('')
const popover = ref<{ stall: Stall; server: string; gem: string; quote: Quote; x: number; y: number; above: boolean } | null>(null)
let timer = 0
const gemIcons: Record<string, string> = {
  光芒石: lightGem, 月亮石: moonstone, 太阳石: sunstone, 红玛瑙: redAgate,
  五色灵尘: fiveColorDust, 星辉石: starGem, 黑宝石: blackGem, 舍利子: relic,
}
const gemNames = Object.keys(gemIcons)
const groups = computed(() => {
  const result = new Map<string, Stall[]>()
  for (const stall of stalls.value) result.set(stall.server_name, [...(result.get(stall.server_name) || []), stall])
  return [...result].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], 'zh-CN'))
})
const market = (server: string, gem: string) => merchants.value
  .filter(item => item.server_name === server)
  .map(item => ({ merchant: item.stall_owner_id, quote: item.quotes.find(quote => quote.gem_name === gem) }))
  .filter((item): item is { merchant: string; quote: Quote } => !!item.quote)
  .sort((a, b) => a.quote.price_coins - b.quote.price_coins)
const average = (items: { quote: Quote }[]) => items.length >= 2
  ? Math.round(items.reduce((sum, item) => sum + item.quote.price_coins, 0) / items.length) : null
const quoteFor = (stall: Stall, gem: string) => stall.quotes.find(quote => quote.gem_name === gem)
const money = (value: number) => `${(value / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万`
function ownPriceOutlier(server: string, gem: string, price: number) {
  const prices = stalls.value.filter(item => item.server_name === server)
    .map(item => quoteFor(item, gem)?.price_coins).filter((value): value is number => value != null)
  const counts = new Map<number, number>()
  for (const value of prices) counts.set(value, (counts.get(value) || 0) + 1)
  if (counts.size <= 1) return false
  const highestFrequency = Math.max(...counts.values())
  const commonPrices = [...counts].filter(([, count]) => count === highestFrequency)
  return highestFrequency === 1 || commonPrices.length !== 1 || price !== commonPrices[0][0]
}
function belowMarket(server: string, gem: string, price: number) {
  const value = average(market(server, gem))
  return value != null && price < value
}
const popoverMarket = computed(() => popover.value ? market(popover.value.server, popover.value.gem) : [])
const popoverAverage = computed(() => average(popoverMarket.value))
const popoverStyle = computed(() => popover.value ? {
  left: `${popover.value.x}px`, top: `${popover.value.y}px`,
  transform: popover.value.above ? 'translate(-50%, calc(-100% - 10px))' : 'translate(-50%, 10px)',
} : {})
function showPopover(event: MouseEvent | FocusEvent, stall: Stall, server: string, gem: string, quote: Quote) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const width = Math.min(330, window.innerWidth - 24)
  const x = Math.max(width / 2 + 12, Math.min(rect.left + rect.width / 2, window.innerWidth - width / 2 - 12))
  const above = rect.bottom + 300 > window.innerHeight && rect.top > 300
  popover.value = { stall, server, gem, quote, x, y: above ? rect.top : rect.bottom, above }
}
function hidePopover() { popover.value = null }
async function takeOffline(stall: Stall) {
  if (!window.confirm(`确认将“${stall.role_name || stall.character_id}”标记为摊位下线吗？\n\n下线后会从“我的摊位”移除；客户端再次检测到该角色打开收货摊位时会自动恢复。`)) return
  removing.value = stall.character_id
  hidePopover()
  try {
    await api(`/api/v1/master/own-stalls/${encodeURIComponent(stall.character_id)}`, { method: 'DELETE' })
    stalls.value = stalls.value.filter(item => item.character_id !== stall.character_id)
    error.value = ''
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { removing.value = '' }
}
async function refresh(silent = false) {
  if (!silent) loading.value = true
  try {
    const result = await api<{stalls: Stall[]; merchants: Merchant[]}>('/api/v1/master/own-stalls')
    stalls.value = result.stalls || []
    merchants.value = result.merchants || []
    error.value = ''
    refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { loading.value = false }
}
onMounted(() => { void refresh(); timer = window.setInterval(() => void refresh(true), 5000) })
onUnmounted(() => window.clearInterval(timer))
</script>

<template><div class="my-stalls">
  <div class="stall-toolbar"><div><strong>我的摊位</strong><span>自己的收购价与同区同行报价对比</span></div><span>每 5 秒刷新<span v-if="refreshedAt"> · {{ refreshedAt }}</span></span><button :disabled="loading" @click="refresh()">立即刷新</button></div>
  <p v-if="error" class="message error">{{ error }}</p><p v-else-if="loading" class="empty">正在读取我的摊位…</p>
  <template v-else-if="groups.length"><Panel v-for="[server, items] in groups" :key="server" :title="server" :meta="`${items.length} 个收货角色`">
    <div class="stall-table-wrap"><table class="stall-table"><thead><tr><th class="gem-column">一级宝石</th><th v-for="stall in items" :key="stall.character_id" class="account-column"><strong>{{ stall.role_name || '未知角色' }}</strong><small>ID {{ stall.character_id }}</small><small>{{ time(stall.latest_at) }}</small><button class="offline-button" :disabled="removing === stall.character_id" @click="takeOffline(stall)">{{ removing === stall.character_id ? '处理中…' : '摊位下线' }}</button></th></tr></thead><tbody>
      <tr v-for="gem in gemNames" :key="gem"><td class="gem-column"><span class="gem-heading"><img :src="gemIcons[gem]" alt="">{{ gem }}</span></td>
        <td v-for="stall in items" :key="stall.character_id"><div v-if="quoteFor(stall, gem)" :class="['price-label', { 'own-outlier': ownPriceOutlier(server, gem, quoteFor(stall, gem)!.price_coins) }]" tabindex="0" @mouseenter="showPopover($event, stall, server, gem, quoteFor(stall, gem)!)" @focus="showPopover($event, stall, server, gem, quoteFor(stall, gem)!)" @mouseleave="hidePopover" @blur="hidePopover"><strong>{{ money(quoteFor(stall, gem)!.price_coins) }}</strong><small v-if="belowMarket(server, gem, quoteFor(stall, gem)!.price_coins)">低于同行均价</small></div><span v-else class="missing">—</span></td>
      </tr>
    </tbody></table></div>
  </Panel></template>
  <p v-else class="empty">暂无自己的摊位价格。打开自己的收货摊位并保持画面稳定，识别结果会自动显示在这里。</p>
  <Teleport to="body"><div v-if="popover" class="stall-price-popover" :style="popoverStyle">
    <template v-if="popoverMarket.length">
      <div class="comparison-head"><span>{{ popover.gem }} · 本区同行</span><strong>{{ popoverAverage == null ? '至少 2 个摊主才计算均价' : `均价 ${money(popoverAverage)}` }}</strong></div>
      <div v-if="popoverAverage != null" class="difference" :class="{high: popover.quote.price_coins > popoverAverage}">我的价格比均价 {{ popover.quote.price_coins >= popoverAverage ? '高' : '低' }} {{ money(Math.abs(popover.quote.price_coins - popoverAverage)) }}</div>
      <ul><li v-for="item in popoverMarket" :key="item.merchant"><span>摊主 {{ item.merchant }}</span><strong>{{ money(item.quote.price_coins) }}</strong><small>{{ time(item.quote.observed_at) }}</small></li></ul>
    </template><p v-else>本区暂未采集到其他商人的该商品报价</p>
  </div></Teleport>
</div></template>

<style scoped>
.my-stalls{display:grid;gap:14px}.stall-toolbar{display:flex;align-items:center;gap:16px;padding:12px 16px;border:1px solid var(--line);border-radius:14px;background:#fff}.stall-toolbar>div{display:grid;margin-right:auto}.stall-toolbar div span,.stall-toolbar>span{color:var(--ink-soft);font-size:11px}.stall-toolbar button{border:0;border-radius:9px;padding:8px 13px;background:#eef4ff;color:#2563db;font-weight:750}.stall-table-wrap{overflow-x:auto}.stall-table{width:max-content;min-width:0;border-collapse:separate;border-spacing:0}.stall-table th,.stall-table td{box-sizing:border-box;width:150px;min-width:150px;max-width:150px;height:54px;padding:6px 10px;text-align:center;border-bottom:1px solid var(--line);white-space:nowrap}.stall-table th{height:82px;background:#f7f9fd;color:var(--ink-soft);font-size:11px}.stall-table tbody tr:hover{background:#f8fbff}.stall-table .gem-column{position:sticky;left:0;z-index:3;width:122px;min-width:122px;max-width:122px;text-align:left;background:#fff}.stall-table th.gem-column{z-index:4;background:#f7f9fd}.account-column strong,.account-column small{display:block}.account-column strong{color:var(--ink);font-size:12px}.account-column small{margin-top:2px;color:var(--ink-soft);font-size:9px}.offline-button{margin-top:6px;border:1px solid #f0b8b8;border-radius:7px;padding:3px 9px;background:#fff5f5;color:#c24141;font-size:10px;cursor:pointer}.offline-button:hover{border-color:#dc2626;background:#fee2e2}.offline-button:disabled{cursor:wait;opacity:.55}.gem-heading{display:flex;align-items:center;gap:7px;font-weight:750}.gem-heading img{width:28px;height:28px;object-fit:contain}.price-label{display:inline-flex;align-items:center;justify-content:center;gap:5px;min-width:88px;min-height:28px;padding:3px 8px;border-radius:999px;background:#edf3fa;cursor:help;outline:none}.price-label>strong{color:#17213b;font-size:12px;font-variant-numeric:tabular-nums}.price-label>small{color:#dc2626;font-size:9px;font-weight:750}.price-label:hover,.price-label:focus{background:#dce9fb;box-shadow:0 3px 10px #1d4ed81c}.price-label.own-outlier{background:#fee2e2;box-shadow:inset 0 0 0 1px #fca5a5}.price-label.own-outlier>strong{color:#b91c1c}.missing{color:#c3cad5}.stall-price-popover{position:fixed;z-index:10000;width:min(330px,calc(100vw - 24px));max-height:min(360px,calc(100vh - 24px));overflow:hidden;padding:12px;border-radius:12px;background:#17213b;color:#fff;box-shadow:0 14px 34px #0f172a66;text-align:left;white-space:normal;pointer-events:none}.comparison-head{display:flex;justify-content:space-between;gap:8px;font-size:12px}.difference{margin:8px 0;padding:7px;border-radius:7px;background:#14532d;font-size:11px}.difference.high{background:#7f1d1d}.stall-price-popover ul{display:grid;gap:5px;max-height:240px;overflow:auto;margin:0;padding:0;list-style:none}.stall-price-popover li{display:grid;grid-template-columns:1fr auto;gap:2px 8px;padding-top:5px;border-top:1px solid #ffffff1f}.stall-price-popover li span,.stall-price-popover li strong{font-size:11px}.stall-price-popover li small{grid-column:1/-1;color:#b9c7dd}.stall-price-popover p{margin:0;color:#d8e2f4;font-size:11px}@media(max-width:800px){.stall-toolbar>span{display:none}.stall-table th,.stall-table td{width:132px;min-width:132px;max-width:132px}}
</style>
