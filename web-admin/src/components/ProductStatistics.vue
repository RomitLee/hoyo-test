<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
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

type Summary = { server_name: string; gem_name: string; merchant_count: number; average_price: number | null; minimum_price: number; maximum_price: number; latest_at: string }
type Quote = { gem_name: string; price_coins: number; observed_at?: string }
type Merchant = { server_name: string; stall_owner_id: string; observer_character_id?: string | null; latest_at?: string; screenshot_log_id?: number; quotes: Quote[] }
const items = ref<Summary[]>([])
const merchants = ref<Merchant[]>([])
const activeServer = ref('')
const loading = ref(true)
const error = ref('')
const refreshedAt = ref('')
const preview = ref('')
const previewLoading = ref(false)
let refreshTimer = 0
const gemIcons: Record<string, string> = {
  光芒石: lightGem, 月亮石: moonstone, 太阳石: sunstone, 红玛瑙: redAgate,
  五色灵尘: fiveColorDust, 星辉石: starGem, 黑宝石: blackGem, 舍利子: relic,
}
const gemNames = Object.keys(gemIcons)
const servers = computed(() => {
  const counts = new Map<string, number>()
  for (const merchant of merchants.value) counts.set(merchant.server_name, (counts.get(merchant.server_name) || 0) + 1)
  for (const item of items.value) if (!counts.has(item.server_name)) counts.set(item.server_name, 0)
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN'))
    .map(([name, count]) => ({ name, count }))
})
watch(servers, value => {
  if (!value.some(server => server.name === activeServer.value)) activeServer.value = value[0]?.name || ''
}, { immediate: true })
const activeMerchants = computed(() => merchants.value.filter(item => item.server_name === activeServer.value))
const activeSummary = computed(() => items.value.filter(item => item.server_name === activeServer.value))
const money = (value: number) => `${(value / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万`
const quoteFor = (merchant: Merchant, gem: string) => merchant.quotes.find(quote => quote.gem_name === gem)
const latestFor = (merchant: Merchant) => merchant.quotes.reduce((latest, quote) => {
  const value = quote.observed_at || ''
  return value > latest ? value : latest
}, merchant.latest_at || '')
async function openScreenshot(merchant: Merchant) {
  if (!merchant.screenshot_log_id) return
  previewLoading.value = true
  try {
    const result = await api<{url: string}>(`/api/v1/master/monitor-logs/${merchant.screenshot_log_id}/screenshot`)
    preview.value = result.url
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { previewLoading.value = false }
}
async function refresh(silent = false) {
  if (!silent) loading.value = true
  try {
    const result = await api<{items: Summary[]; merchants: Merchant[]}>('/api/v1/master/market-gem-statistics')
    items.value = result.items
    merchants.value = result.merchants || []
    error.value = ''
    refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { loading.value = false }
}
onMounted(() => {
  void refresh()
  refreshTimer = window.setInterval(() => void refresh(true), 5000)
})
onUnmounted(() => window.clearInterval(refreshTimer))
</script>

<template><div class="product-statistics">
  <div class="sub-tabs"><button class="active">宝石统计</button><span>每 5 秒自动刷新<span v-if="refreshedAt"> · 更新于 {{ refreshedAt }}</span></span><button class="refresh" :disabled="loading" @click="refresh()">立即刷新</button></div>
  <div class="gem-catalog" aria-label="监控宝石类型"><span v-for="name in gemNames" :key="name"><img :src="gemIcons[name]" alt="">{{ name }}</span></div>
  <p v-if="error" class="message error">{{ error }}</p><p v-else-if="loading" class="empty">正在读取商品数据…</p>
  <template v-else-if="servers.length">
    <nav class="server-tabs" aria-label="区服报价"><button v-for="server in servers" :key="server.name" :class="{active: activeServer === server.name}" @click="activeServer = server.name">{{ server.name }} <small>{{ server.count }} 个摊主</small></button></nav>
    <Panel :title="activeServer" :meta="`${activeMerchants.length} 个同行摊主 · 已排除自己的收货角色`">
      <div class="statistics-layout"><section class="summary-pane"><h3>区服汇总</h3>
        <div class="table-wrap"><table><thead><tr><th>一级宝石</th><th>同行均价</th><th>最低价</th><th>最高价</th><th>摊主数</th></tr></thead><tbody><tr v-for="item in activeSummary" :key="item.gem_name"><td class="gem-name"><img :src="gemIcons[item.gem_name]" alt="">{{ item.gem_name }}</td><td><strong>{{ item.average_price == null ? '—' : money(item.average_price) }}</strong><small v-if="item.average_price == null">至少 2 个摊主</small></td><td>{{ money(item.minimum_price) }}</td><td>{{ money(item.maximum_price) }}</td><td>{{ item.merchant_count }}</td></tr></tbody></table></div>
      </section><section class="merchant-pane"><h3>摊主详细报价</h3>
        <div class="table-wrap merchant-table"><table><thead><tr><th>摊主 ID</th><th v-for="gem in gemNames" :key="gem"><img :src="gemIcons[gem]" alt="">{{ gem }}</th><th>最近识别</th></tr></thead><tbody>
          <tr v-for="merchant in activeMerchants" :key="merchant.stall_owner_id"><td><div class="merchant-id"><strong>{{ merchant.stall_owner_id }}</strong><button v-if="merchant.screenshot_log_id" :disabled="previewLoading" @click="openScreenshot(merchant)">查看截图</button></div><small v-if="merchant.observer_character_id">由角色 {{ merchant.observer_character_id }} 采集</small></td><td v-for="gem in gemNames" :key="gem" :class="{muted: !quoteFor(merchant, gem)}">{{ quoteFor(merchant, gem) ? money(quoteFor(merchant, gem)!.price_coins) : '—' }}</td><td>{{ time(latestFor(merchant)) }}</td></tr>
        </tbody></table></div><p v-if="!activeMerchants.length" class="empty">该区服暂无摊主明细。</p>
      </section></div>
    </Panel>
  </template>
  <p v-else class="empty">暂无宝石报价。客户端识别到其他收购商摊位后会自动出现。</p>
  <Teleport to="body"><div v-if="preview" class="screenshot-modal" @click.self="preview = ''"><div><button @click="preview = ''">关闭</button><img :src="preview" alt="摊主收货摊位截图" referrerpolicy="no-referrer"></div></div></Teleport>
</div></template>

<style scoped>
.product-statistics{display:grid;gap:14px}.sub-tabs{display:flex;align-items:center;gap:14px;padding:5px;border:1px solid var(--line);border-radius:14px;background:#fff}.sub-tabs button{height:38px;padding:0 18px;border:0;border-radius:10px;background:#17213b;color:#fff;font-weight:750}.sub-tabs button.refresh{margin-left:auto;background:#eef4ff;color:#2563db}.sub-tabs span{color:var(--ink-soft);font-size:11px}.gem-catalog{display:grid;grid-template-columns:repeat(8,minmax(72px,1fr));gap:8px}.gem-catalog span{display:flex;flex-direction:column;align-items:center;gap:5px;padding:8px 4px;border:1px solid var(--line);border-radius:12px;background:#fff;font-size:12px;font-weight:700}.gem-catalog img{width:52px;height:52px;object-fit:contain}.server-tabs{display:flex;gap:8px;overflow-x:auto;padding:4px 2px}.server-tabs button{white-space:nowrap;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink);padding:10px 14px;font-weight:750}.server-tabs button.active{border-color:#2563db;background:#2563db;color:#fff}.server-tabs small{display:block;margin-top:3px;font-size:10px;opacity:.72}.statistics-layout{display:grid;grid-template-columns:minmax(390px,38%) minmax(0,62%);gap:18px;align-items:start}.statistics-layout h3{margin:0 0 9px}.summary-pane,.merchant-pane{min-width:0}.summary-pane table{min-width:560px}.summary-pane td small{display:block;color:var(--ink-soft);font-size:9px}.merchant-table th,.merchant-table td{text-align:center;white-space:nowrap}.merchant-table th:first-child,.merchant-table td:first-child{text-align:left;position:sticky;left:0;background:#fff;z-index:1}.merchant-table th img{display:block;width:28px;height:28px;object-fit:contain;margin:0 auto 3px}.merchant-table td small{display:block;color:var(--ink-soft);font-size:10px}.merchant-table .muted{color:#c3cad5}.merchant-id{display:flex;align-items:center;gap:7px}.merchant-id button{border:0;border-radius:6px;padding:3px 7px;background:#e8f1ff;color:#2563db;font-size:9px;font-weight:750}.gem-name{display:flex;align-items:center;gap:8px}.gem-name img{width:32px;height:32px;object-fit:contain;border-radius:8px;background:#f5f7fb}td strong{font-variant-numeric:tabular-nums}.screenshot-modal{position:fixed;inset:0;z-index:11000;display:grid;place-items:center;padding:24px;background:#0f172acc}.screenshot-modal>div{display:grid;gap:8px;max-width:min(900px,95vw);max-height:95vh}.screenshot-modal button{justify-self:end;border:0;border-radius:8px;padding:7px 13px;background:#fff;color:#17213b;font-weight:750}.screenshot-modal img{display:block;max-width:100%;max-height:88vh;border-radius:10px;background:#111827;object-fit:contain}@media(max-width:1200px){.statistics-layout{grid-template-columns:1fr}.gem-catalog{grid-template-columns:repeat(4,1fr)}}
</style>
