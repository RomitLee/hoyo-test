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
let timer = 0
const gemIcons: Record<string, string> = {
  光芒石: lightGem, 月亮石: moonstone, 太阳石: sunstone, 红玛瑙: redAgate,
  五色灵尘: fiveColorDust, 星辉石: starGem, 黑宝石: blackGem, 舍利子: relic,
}
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
const average = (items: { quote: Quote }[]) => items.length
  ? Math.round(items.reduce((sum, item) => sum + item.quote.price_coins, 0) / items.length) : null
const money = (value: number) => `${(value / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万`
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
    <div class="stall-grid"><article v-for="stall in items" :key="stall.character_id" class="stall-card">
      <header><div><strong>{{ stall.role_name || '未知角色' }}</strong><small>账号 ID：{{ stall.character_id }}</small></div><time>{{ time(stall.latest_at) }}</time></header>
      <div class="quote-grid"><div v-for="quote in stall.quotes" :key="quote.gem_name" class="quote" tabindex="0">
        <img :src="gemIcons[quote.gem_name]" :alt="quote.gem_name"><span>{{ quote.gem_name }}</span><strong>{{ money(quote.price_coins) }}</strong>
        <div class="comparison"><template v-if="market(server, quote.gem_name).length">
          <div class="comparison-head"><span>本区其他商人</span><strong>均价 {{ money(average(market(server, quote.gem_name))!) }}</strong></div>
          <div class="difference" :class="{high: quote.price_coins > average(market(server, quote.gem_name))!}">我的价格比均价 {{ quote.price_coins >= average(market(server, quote.gem_name))! ? '高' : '低' }} {{ money(Math.abs(quote.price_coins - average(market(server, quote.gem_name))!)) }}</div>
          <ul><li v-for="item in market(server, quote.gem_name)" :key="item.merchant"><span>摊主 {{ item.merchant }}</span><strong>{{ money(item.quote.price_coins) }}</strong><small>{{ time(item.quote.observed_at) }}</small></li></ul>
        </template><p v-else>本区暂未采集到其他商人的该商品报价</p></div>
      </div></div>
    </article></div>
  </Panel></template>
  <p v-else class="empty">暂无自己的摊位价格。打开自己的收货摊位并保持画面稳定，识别结果会自动显示在这里。</p>
</div></template>

<style scoped>
.my-stalls{display:grid;gap:14px}.stall-toolbar{display:flex;align-items:center;gap:16px;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:#fff}.stall-toolbar>div{display:grid;margin-right:auto}.stall-toolbar div span,.stall-toolbar>span{color:var(--ink-soft);font-size:11px}.stall-toolbar button{border:0;border-radius:9px;padding:9px 14px;background:#eef4ff;color:#2563db;font-weight:750}.stall-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.stall-card{border:1px solid var(--line);border-radius:14px;padding:14px;background:#fbfcff}.stall-card header{display:flex;align-items:start;justify-content:space-between;margin-bottom:12px}.stall-card header div{display:grid}.stall-card small,.stall-card time{color:var(--ink-soft);font-size:10px}.quote-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.quote{position:relative;display:grid;justify-items:center;gap:3px;padding:9px 4px;border:1px solid #dce5f3;border-radius:11px;background:#fff;cursor:help;outline:none}.quote:hover,.quote:focus{border-color:#2563db;box-shadow:0 5px 18px #1d4ed820}.quote img{width:44px;height:44px;object-fit:contain}.quote span{font-size:11px}.quote>strong{font-size:13px;color:#17213b;font-variant-numeric:tabular-nums}.comparison{display:none;position:absolute;left:50%;bottom:calc(100% + 8px);z-index:20;width:310px;transform:translateX(-50%);padding:12px;border-radius:12px;background:#17213b;color:#fff;box-shadow:0 14px 34px #0f172a55;text-align:left}.quote:hover .comparison,.quote:focus .comparison{display:block}.comparison:after{content:'';position:absolute;left:50%;top:100%;border:7px solid transparent;border-top-color:#17213b}.comparison-head{display:flex;justify-content:space-between;gap:8px;font-size:12px}.difference{margin:8px 0;padding:7px;border-radius:7px;background:#14532d;font-size:11px}.difference.high{background:#7f1d1d}.comparison ul{display:grid;gap:5px;max-height:190px;overflow:auto;margin:0;padding:0;list-style:none}.comparison li{display:grid;grid-template-columns:1fr auto;gap:2px 8px;padding-top:5px;border-top:1px solid #ffffff1f}.comparison li span,.comparison li strong{font-size:11px}.comparison li small{grid-column:1/-1;color:#b9c7dd}.comparison p{margin:0;color:#d8e2f4;font-size:11px}@media(max-width:1100px){.quote-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:800px){.stall-grid{grid-template-columns:1fr}.stall-toolbar>span{display:none}}
</style>
