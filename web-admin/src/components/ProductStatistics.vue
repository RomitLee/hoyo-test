<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
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

type Item = { server_name: string; gem_name: string; merchant_count: number; average_price: number; minimum_price: number; maximum_price: number; latest_at: string }
const items = ref<Item[]>([])
const loading = ref(true)
const error = ref('')
const servers = computed(() => [...new Set(items.value.map(item => item.server_name))])
const money = (value: number) => `${(value / 10000).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万`
const gemIcons: Record<string, string> = {
  光芒石: lightGem, 月亮石: moonstone, 太阳石: sunstone, 红玛瑙: redAgate,
  五色灵尘: fiveColorDust, 星辉石: starGem, 黑宝石: blackGem, 舍利子: relic,
}
const gemNames = Object.keys(gemIcons)
onMounted(async () => {
  try { items.value = (await api<{items: Item[]}>('/api/v1/master/market-gem-statistics')).items }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { loading.value = false }
})
</script>

<template><div class="product-statistics">
  <div class="sub-tabs"><button class="active">宝石统计</button><span>后续商品统计将在这里扩展</span></div>
  <div class="gem-catalog" aria-label="监控宝石类型"><span v-for="name in gemNames" :key="name"><img :src="gemIcons[name]" alt="">{{ name }}</span></div>
  <p v-if="error" class="message error">{{ error }}</p><p v-else-if="loading" class="empty">正在读取商品数据…</p>
  <Panel v-for="server in servers" v-else :key="server" :title="server" :meta="`${items.filter(item => item.server_name === server).length} 种宝石`">
    <div class="table-wrap"><table><thead><tr><th>一级宝石</th><th>区服均价</th><th>最低 / 最高</th><th>有效摊主</th><th>最近识别</th></tr></thead><tbody>
      <tr v-for="item in items.filter(row => row.server_name === server)" :key="item.gem_name"><td><span class="gem-name"><img :src="gemIcons[item.gem_name]" alt=""><strong>{{ item.gem_name }}</strong></span></td><td><strong>{{ money(item.average_price) }}</strong></td><td>{{ money(item.minimum_price) }} / {{ money(item.maximum_price) }}</td><td>{{ item.merchant_count }} 个</td><td>{{ time(item.latest_at) }}</td></tr>
    </tbody></table></div>
  </Panel>
  <p v-if="!loading && !error && !items.length" class="empty">暂无宝石报价。客户端识别到其他收购商摊位后会自动出现。</p>
</div></template>

<style scoped>
.product-statistics{display:grid;gap:14px}.sub-tabs{display:flex;align-items:center;gap:14px;padding:5px;border:1px solid var(--line);border-radius:14px;background:#fff}.sub-tabs button{height:38px;padding:0 18px;border:0;border-radius:10px;background:#17213b;color:#fff;font-weight:750}.sub-tabs span{color:var(--ink-soft);font-size:11px}.gem-catalog{display:grid;grid-template-columns:repeat(8,minmax(72px,1fr));gap:8px}.gem-catalog span{display:flex;flex-direction:column;align-items:center;gap:5px;padding:8px 4px;border:1px solid var(--line);border-radius:12px;background:#fff;font-size:12px;font-weight:700}.gem-catalog img{width:52px;height:52px;object-fit:contain}td strong{font-variant-numeric:tabular-nums}.gem-name{display:flex;align-items:center;gap:10px}.gem-name img{width:42px;height:42px;object-fit:contain;border-radius:8px;background:#f5f7fb}@media(max-width:900px){.gem-catalog{grid-template-columns:repeat(4,1fr)}}
</style>
