<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, json, USERNAME_KEY } from '../api'
import { dailyProfit, gemTypes, synthesisRows, type GemKind, type SaleMode } from '../gemMath'
import normalGemIcon from '../assets/gem-normal.png'
import starGemIcon from '../assets/gem-star.png'
import dustGemIcon from '../assets/gem-dust.png'
import Panel from './Panel.vue'
import '../styles/gem.css'

type Zone = { id: string; name: string; quote_yuan: number; purchase_wan: number; margin_percent: number; account_count: number; target_profit_yuan: number }
type Settings = { revision: number; zones: Zone[]; price_ratios: Record<GemKind, Record<string, number>>; synthesis_prices: Record<GemKind, number>; average_sale_prices: Record<GemKind, number> }
const props = defineProps<{ mode: 'merchant' | 'zones' }>()
const settings = ref<Settings | null>(null)
const selectedZoneId = ref('')
const gemKind = ref<GemKind>('normal')
const gemIcons: Record<GemKind, string> = { normal: normalGemIcon, star: starGemIcon, dust: dustGemIcon }
const saleMode = ref<SaleMode>('ratio')
const saving = ref(false)
const loading = ref(true)
const error = ref('')
const notice = ref('')
const editedSinceLoad = ref(false)
const overallQuote = ref<number | string>(0)
const overallAccounts = ref<number | string>(0)
const overallPurchase = ref<number | string>(0)
const overallMargin = ref<number | string>(0)

const selectedZone = computed(() => settings.value?.zones.find(zone => zone.id === selectedZoneId.value) || settings.value?.zones[0])
const quote = computed(() => selectedZone.value?.quote_yuan || 0)
const rows = computed(() => settings.value ? synthesisRows(gemKind.value,
  Number(settings.value.synthesis_prices[gemKind.value]) || 0,
  Number(settings.value.average_sale_prices[gemKind.value]) || 0,
  settings.value.price_ratios[gemKind.value], quote.value, saleMode.value) : [])
const profit = computed(() => selectedZone.value ? dailyProfit(
  Number(selectedZone.value.purchase_wan) || 0, Number(selectedZone.value.margin_percent) || 0,
  Number(selectedZone.value.account_count) || 0, quote.value,
  Number(selectedZone.value.target_profit_yuan) || 0) : null)
const overallValid = computed(() => [overallQuote.value, overallAccounts.value, overallPurchase.value, overallMargin.value]
  .every(value => value !== '' && Number.isFinite(Number(value)))
  && Number(overallQuote.value) > 0 && Number(overallAccounts.value) >= 0
  && Number.isInteger(Number(overallAccounts.value)) && Number(overallPurchase.value) >= 0
  && Number(overallMargin.value) >= 0)
const overallProfit = computed(() => overallValid.value ? dailyProfit(
  Number(overallPurchase.value), Number(overallMargin.value), Number(overallAccounts.value),
  Number(overallQuote.value), 0) : null)
const overallNetYuan = computed(() => overallProfit.value
  ? overallProfit.value.netWan / 3000 * Number(overallQuote.value) : null)

const fmt = (value: number | null, digits = 0) => value == null || !Number.isFinite(value) ? '—'
  : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
const cacheKey = () => `hoyo.gemSettings.${localStorage.getItem(USERNAME_KEY) || ''}`
function cacheSettings(value: Settings) {
  try { sessionStorage.setItem(cacheKey(), JSON.stringify(value)) } catch { /* storage may be unavailable */ }
}
function isSettings(value: unknown): value is Settings {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<Settings>
  return typeof item.revision === 'number' && Array.isArray(item.zones)
    && !!item.price_ratios && !!item.synthesis_prices && !!item.average_sale_prices
}
function applySettings(value: Settings) {
  settings.value = value
  selectedZoneId.value = value.zones.some(z => z.id === selectedZoneId.value)
    ? selectedZoneId.value : value.zones[0]?.id || ''
  if (!overallQuote.value) overallQuote.value = value.zones[0]?.quote_yuan || 0
}

async function load() {
  try {
    const cached = sessionStorage.getItem(cacheKey())
    if (cached) {
      const parsed: unknown = JSON.parse(cached)
      if (isSettings(parsed)) applySettings(parsed)
    }
  } catch { /* a malformed cache simply falls back to the server */ }
  loading.value = !settings.value
  error.value = ''
  try {
    const fresh = await api<Settings>('/api/v1/master/gem-settings')
    if (!editedSinceLoad.value) { applySettings(fresh); cacheSettings(fresh) }
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause)
    error.value = settings.value ? `最新配置加载失败，当前显示本次会话的缓存：${message}` : message
  }
  finally { loading.value = false }
}

async function save() {
  if (!settings.value || saving.value) return
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    settings.value = await api<Settings>('/api/v1/master/gem-settings', json('PUT', settings.value))
    cacheSettings(settings.value)
    notice.value = props.mode === 'zones' ? '区服比例已保存' : '宝石商人参数已保存'
    selectedZoneId.value = settings.value.zones.some(z => z.id === selectedZoneId.value)
      ? selectedZoneId.value : settings.value.zones[0]?.id || ''
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { saving.value = false }
}

function addZone() {
  if (!settings.value) return
  editedSinceLoad.value = true
  let number = settings.value.zones.length + 1
  while (settings.value.zones.some(zone => zone.name === `新区服 ${number}`)) number++
  const id = crypto.randomUUID().replaceAll('-', '')
  settings.value.zones.push({ id, name: `新区服 ${number}`, quote_yuan: 210,
    purchase_wan: 9000, margin_percent: 17, account_count: 3, target_profit_yuan: 30 })
  selectedZoneId.value = id
}

function removeZone(id: string) {
  if (!settings.value) return
  editedSinceLoad.value = true
  settings.value.zones = settings.value.zones.filter(zone => zone.id !== id)
  if (selectedZoneId.value === id) selectedZoneId.value = settings.value.zones[0]?.id || ''
}

onMounted(load)
</script>

<template>
  <div class="gem-workspace" @input.capture="editedSinceLoad = true" @change.capture="editedSinceLoad = true">
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="notice" class="message success" role="status">{{ notice }}</p>
    <p v-if="loading" class="empty">正在读取宝石计算配置…</p>
    <template v-else-if="settings">
      <Panel v-if="mode === 'zones'" title="区服比例" meta="每 3000 万游戏币对应人民币价格，按当前账号保存">
        <p class="hint">宝石合成和单服日利润都使用这里的区服报价。新增、修改或删除后点击保存；不同账号的数据互不影响。</p>
        <div class="table-wrap">
          <table class="gem-zone-table"><thead><tr><th>区服</th><th>3000 万游戏币价格（元）</th><th>操作</th></tr></thead><tbody>
            <tr v-for="zone in settings.zones" :key="zone.id"><td><input v-model.trim="zone.name" aria-label="区服名称" maxlength="32" required></td><td><input v-model.number="zone.quote_yuan" type="number" min="0.01" max="100000" step="0.01" :aria-label="`${zone.name}区服比例`" required></td><td><button type="button" class="secondary small" @click="removeZone(zone.id)">删除</button></td></tr>
          </tbody></table>
        </div>
        <p v-if="!settings.zones.length" class="empty">尚未设置区服，请先新增。</p>
        <div class="gem-actions"><button type="button" class="secondary" :disabled="settings.zones.length >= 50" @click="addZone">新增区服</button><button type="button" class="primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存区服比例' }}</button></div>
      </Panel>

      <div v-else class="gem-merchant-shell">
        <div class="gem-merchant-banner">
          <div><span class="gem-banner-kicker">GEM MERCHANT · 经营工具</span><h2>宝石商人工作台</h2><p>合成定价与日利润测算，使用同一套区服报价</p></div>
          <span class="gem-banner-count">{{ settings.zones.length }} 个区服已配置</span>
        </div>
      <div class="gem-merchant-grid">
        <Panel class="gem-synthesis-panel" title="宝石合成计算器" meta="按一级价格自动换算">
          <p v-if="!settings.zones.length" class="empty">请先到“设置”中新增区服比例。</p>
          <template v-else>
            <div class="gem-type-tabs" role="group" aria-label="宝石类型">
              <button v-for="(item, kind) in gemTypes" :key="kind" type="button" :class="['gem-type-tab', { active: gemKind === kind }]" :aria-pressed="gemKind === kind" @click="gemKind = kind"><img :src="gemIcons[kind]" alt="">{{ item.name }}</button>
            </div>
            <div class="gem-fields">
              <label>应用区服<select v-model="selectedZoneId"><option v-for="zone in settings.zones" :key="zone.id" :value="zone.id">{{ zone.name }}（3000 万 = {{ fmt(zone.quote_yuan) }} 元）</option></select></label>
              <label>一级宝石价格（游戏币）<input v-model.number="settings.synthesis_prices[gemKind]" type="number" min="0" max="1000000000" step="1"></label>
              <label v-if="saleMode === 'average'">一级售卖均价（游戏币）<input v-model.number="settings.average_sale_prices[gemKind]" type="number" min="0" max="1000000000" step="1"></label>
            </div>
            <div class="gem-sale-tabs" role="group" aria-label="售卖方式"><button type="button" :class="{ active: saleMode === 'ratio' }" :aria-pressed="saleMode === 'ratio'" @click="saleMode = 'ratio'">按比例售卖</button><button type="button" :class="{ active: saleMode === 'average' }" :aria-pressed="saleMode === 'average'" @click="saleMode = 'average'">按均价售卖</button></div>
            <p class="gem-rule">{{ gemTypes[gemKind].rule }}；藏宝阁 {{ gemTypes[gemKind].cbgMinLevel }} 级起可上架，到手按售价的 95% 估算。</p>
            <div class="gem-price-heading"><strong>等级价格表</strong><span>摆摊售价向下取整到万</span></div>
            <div class="gem-price-table-wrap">
              <table class="gem-price-table"><thead><tr><th>等级</th><th>合成成本</th><th>{{ saleMode === 'ratio' ? '售卖比例 / 利润' : '利润率' }}</th><th>摆摊售价</th><th>藏宝阁售价</th></tr></thead><tbody>
                <tr v-for="row in rows" :key="row.level"><td><strong class="gem-level-inline">{{ row.level }} 级</strong><small>{{ fmt(row.units) }} 颗一级</small></td><td><strong>{{ fmt(row.costCoins / 10000, 2) }} 万</strong><small>≈ {{ fmt(row.costYuan, 1) }} 元</small></td><td><input v-if="saleMode === 'ratio'" v-model.number="settings.price_ratios[gemKind][String(row.level)]" type="number" min="0" max="100" step="0.01" :aria-label="`${gemTypes[gemKind].name}${row.level}级售卖比例`"><strong v-else>{{ fmt(row.marginPercent, 2) }}%</strong><small v-if="saleMode === 'ratio'">利润 {{ fmt(row.marginPercent, 2) }}%</small></td><td><strong class="gem-stall-price">{{ fmt(row.stallWan) }} 万</strong></td><td><strong>{{ row.cbgPrice == null ? '不可上架' : `${fmt(row.cbgPrice, 1)} 元` }}</strong><small v-if="row.cbgAfterFee != null">到手 {{ fmt(row.cbgAfterFee, 1) }} 元</small></td></tr>
              </tbody></table>
            </div>
            <div class="gem-actions"><button type="button" class="gem-save" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存宝石价格与售卖比例' }}</button></div>
          </template>
        </Panel>
        <div class="gem-profit-column">
        <Panel class="gem-single-panel" title="单服日利润" meta="只计算当前选择的区服">
          <p v-if="!settings.zones.length" class="empty">请先到“设置”中新增区服比例。</p>
          <template v-else-if="selectedZone && profit">
            <div :class="['gem-net', { loss: profit.netWan < 0 }]"><span>预计每日净利润</span><strong>{{ fmt(profit.netWan, 2) }} 万游戏币</strong><small>{{ selectedZone.name }} · 已扣点卡</small></div>
            <div class="gem-fields">
              <label>区服<select v-model="selectedZoneId"><option v-for="zone in settings.zones" :key="zone.id" :value="zone.id">{{ zone.name }}（3000 万 = {{ fmt(zone.quote_yuan) }} 元）</option></select></label>
              <label>每天收购金额（万）<input v-model.number="selectedZone.purchase_wan" type="number" min="0" max="100000000" step="100"></label>
              <label>实际利润率（%）<input v-model.number="selectedZone.margin_percent" type="number" min="0" max="1000" step="0.1"></label>
              <label>账号配置<select v-model.number="selectedZone.account_count"><option v-for="count in [1,2,3,4,5]" :key="count" :value="count">{{ count === 1 ? '1 收' : `${count - 1} 收 1 卖` }}（{{ count }} 个号）</option></select></label>
              <label>目标每天净利润（元）<input v-model.number="selectedZone.target_profit_yuan" type="number" min="0" max="100000000" step="1"></label>
            </div>
            <div class="gem-results"><div><small>游戏内销售额</small><strong>{{ fmt(profit.salesWan, 2) }} 万</strong></div><div class="highlight"><small>交易毛利润</small><strong>{{ fmt(profit.grossWan, 2) }} 万</strong></div><div><small>藏宝阁折算</small><strong>{{ fmt(profit.cbgYuan, 2) }} 元</strong></div><div class="warning"><small>每天点卡</small><strong>{{ fmt(profit.cardYuan, 1) }} 元</strong><small>≈ {{ fmt(profit.cardWan, 2) }} 万游戏币</small></div><div><small>不亏本收购线</small><strong>{{ fmt(profit.breakEvenWan, 2) }} 万</strong></div><div><small>距离保本线</small><strong>{{ fmt(profit.gapWan, 2) }} 万</strong></div></div>
            <div class="gem-target">若目标每天净利 {{ fmt(selectedZone.target_profit_yuan) }} 元，每天至少收购 <strong>{{ fmt(profit.targetPurchaseWan, 2) }} 万</strong></div>
            <p class="hint">净利（万游戏币）＝收购额 × 利润率 − 点卡折算游戏币。点卡按每个号 0.6 元/小时、每天 24 小时计算。</p>
            <div class="gem-actions"><button type="button" class="gem-save" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存当前区服设置' }}</button></div>
          </template>
        </Panel>
        <Panel class="gem-overall-panel" title="综合测算" meta="汇总所有账号一天的收购与点卡支出">
          <div class="gem-fields">
            <label>3000 万游戏币比例（元）<input v-model.number="overallQuote" type="number" min="0.01" step="0.01" inputmode="decimal"></label>
            <label>总账号数量（个）<input v-model.number="overallAccounts" type="number" min="0" step="1" inputmode="numeric"></label>
            <label>一天总收购金额（万游戏币）<input v-model.number="overallPurchase" type="number" min="0" step="100" inputmode="decimal"></label>
            <label>实际利润率（%）<input v-model.number="overallMargin" type="number" min="0" step="0.1" inputmode="decimal"></label>
          </div>
          <template v-if="overallProfit">
            <div :class="['gem-net', { loss: overallProfit.netWan < 0 }]"><span>预计一天净利润</span><strong>{{ fmt(overallProfit.netWan, 2) }} 万游戏币</strong><strong class="gem-rmb">≈ {{ fmt(overallNetYuan, 2) }} 元</strong><small>已扣除全部账号点卡</small></div>
            <div class="gem-results gem-overall-results"><div><small>交易毛利润</small><strong>{{ fmt(overallProfit.grossWan, 2) }} 万游戏币</strong></div><div><small>点卡支出</small><strong>{{ fmt(overallProfit.cardYuan, 2) }} 元</strong><small>≈ {{ fmt(overallProfit.cardWan, 2) }} 万游戏币</small></div></div>
          </template>
          <p v-else class="hint">请填写有效比例、账号数量、总收购金额和利润率。</p>
          <p class="hint">净利润＝总收购金额 × 利润率 − 总账号数 × 0.6 元/小时 × 24 小时折算的游戏币；人民币按填写的 3000 万比例换算。结果随输入实时更新。</p>
        </Panel>
        </div>
      </div>
      </div>
    </template>
  </div>
</template>
