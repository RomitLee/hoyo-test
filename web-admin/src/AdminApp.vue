<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { estimateStamina } from './stamina'
import { time } from './api'

type Section = 'accounts' | 'roles' | 'devices'
type Account = { account_id: number; username: string; display_name?: string; created_at: string; last_login_at?: string; disabled: boolean; is_admin: boolean; device_count: number; role_count: number }
type Device = { device_id: string; account_id: number; account_username: string; display_name?: string; hostname?: string; app_version?: string; last_seen_at?: string; disabled: boolean; window_count: number }
type Role = { character_id: string; server_name?: string; role_name?: string; account_id: number; account_username: string; account_name?: string; account_count: number; device_name?: string; hostname?: string; device_last_seen_at?: string; role_online: boolean; status?: string; empty_slots?: number; cash?: number; money_confirmed_at?: string; received_at?: string; stamina_current?: number; stamina_max?: number; stamina_recovery_per_5m?: number; stamina_observed_at?: string; stamina_full_at?: string; stall_mode?: string; sell_stock_count?: number; last_seen_at: string }
type Overview = { accounts: number; active_accounts: number; devices: number; online_devices: number; roles: number; online_roles: number }
type Page<T> = { items: T[]; total: number }

const token = ref(localStorage.getItem('hoyo.adminToken') || '')
const username = ref('')
const password = ref('')
const operator = ref('')
const section = ref<Section>('accounts')
const search = ref('')
const accountFilter = ref<number | ''>('')
const offset = ref(0)
const limit = 50
const overview = ref<Overview>({ accounts: 0, active_accounts: 0, devices: 0, online_devices: 0, roles: 0, online_roles: 0 })
const accounts = ref<Page<Account>>({ items: [], total: 0 })
const roles = ref<Page<Role>>({ items: [], total: 0 })
const devices = ref<Page<Device>>({ items: [], total: 0 })
const busy = ref(false)
const error = ref('')
const refreshedAt = ref('')
const nowMs = ref(Date.now())
let timer: number | undefined
let requestId = 0

const current = computed(() => section.value === 'accounts' ? accounts.value : section.value === 'roles' ? roles.value : devices.value)
const online = (stamp?: string) => !!stamp && Date.now() - Date.parse(stamp) < 20_000
const money = (value?: number | null) => value == null ? '未识别' : Number(value).toLocaleString('zh-CN')
const stamina = (role: Role) => estimateStamina(role, nowMs.value)

async function request<T>(path: string, bearer = token.value, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  if (options.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...options, headers, cache: 'no-store' })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(result.detail || `请求失败（${response.status}）`)
  return result as T
}

function signOut(message = '') {
  ++requestId
  localStorage.removeItem('hoyo.adminToken')
  token.value = ''
  operator.value = ''
  password.value = ''
  error.value = message
  if (timer) window.clearInterval(timer)
  timer = undefined
}

async function signIn() {
  busy.value = true
  error.value = ''
  try {
    const login = await request<{ access_token: string }>('/api/v1/auth/login', '', {
      method: 'POST', body: JSON.stringify({ username: username.value.trim(), password: password.value }),
    })
    const me = await request<{ account: { username: string; display_name?: string } }>('/api/v1/admin/me', login.access_token)
    localStorage.setItem('hoyo.adminToken', login.access_token)
    token.value = login.access_token
    operator.value = me.account.display_name || me.account.username
    password.value = ''
    await refresh()
    if (token.value) startTimer()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { busy.value = false }
}

async function refresh() {
  if (!token.value) return
  const id = ++requestId
  const selected = section.value
  busy.value = true
  try {
    const query = new URLSearchParams({ search: search.value.trim(), limit: String(limit), offset: String(offset.value) })
    if (selected !== 'accounts' && accountFilter.value !== '') query.set('account_id', String(accountFilter.value))
    const [summary, page] = await Promise.all([
      request<Overview>('/api/v1/admin/overview'),
      request<Page<Account> | Page<Role> | Page<Device>>(`/api/v1/admin/${selected}?${query}`),
    ])
    if (id !== requestId) return
    overview.value = summary
    if (selected === 'accounts') accounts.value = page as Page<Account>
    if (selected === 'roles') roles.value = page as Page<Role>
    if (selected === 'devices') devices.value = page as Page<Device>
    refreshedAt.value = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    nowMs.value = Date.now()
    error.value = ''
  } catch (cause) {
    if (id !== requestId) return
    const message = cause instanceof Error ? cause.message : String(cause)
    if (message.includes('登录已失效') || message.includes('没有总后台权限') || message.includes('请先登录')) signOut(message)
    else error.value = message
  } finally { busy.value = false }
}

function startTimer() {
  if (timer) window.clearInterval(timer)
  timer = window.setInterval(() => void refresh(), 15_000)
}

function select(next: Section) {
  section.value = next
  search.value = ''
  accountFilter.value = ''
  offset.value = 0
  void refresh()
}

function viewAccount(accountId: number, next: 'roles' | 'devices') {
  section.value = next
  search.value = ''
  accountFilter.value = accountId
  offset.value = 0
  void refresh()
}

function changePage(next: number) {
  offset.value = next
  void refresh()
}

onMounted(async () => {
  if (!token.value) return
  try {
    const me = await request<{ account: { username: string; display_name?: string } }>('/api/v1/admin/me')
    operator.value = me.account.display_name || me.account.username
    await refresh()
    startTimer()
  } catch (cause) { signOut(cause instanceof Error ? cause.message : String(cause)) }
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <div v-if="!token" class="login-stage"><section class="login-card m-surface"><span class="eyebrow">XILIAN ASSISTANT / ADMIN</span><h1>西联助手总后台</h1><p>仅供已授权的管理员查看全站账号、角色和设备。</p><form @submit.prevent="signIn"><label>管理员账号<input v-model="username" autocomplete="username" required></label><label>密码<input v-model="password" type="password" autocomplete="current-password" required></label><button class="primary" :disabled="busy" type="submit">{{ busy ? '验证中…' : '登录总后台' }}</button></form><p v-if="error" class="message error" role="alert">{{ error }}</p><a class="admin-back" href="/">返回用户工作台</a></section></div>
  <div v-else class="app-shell">
    <aside class="sidebar m-rail"><div class="brand"><span class="brand-mark">西</span><div><strong>西联助手</strong><small>ADMIN CONSOLE</small></div></div><nav aria-label="总后台板块"><button v-for="item in [{key:'accounts',label:'账号管理',hint:'全站用户'},{key:'roles',label:'游戏角色',hint:'全局游戏 ID'},{key:'devices',label:'设备运行',hint:'所有从机'}]" :key="item.key" :class="['nav-item',{active:section===item.key}]" @click="select(item.key as Section)"><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></button></nav><div class="sidebar-foot"><span>{{ operator }}</span><button @click="signOut()">退出</button></div></aside>
    <div class="workspace"><header class="topbar"><div><span class="eyebrow">XILIAN ASSISTANT / ADMIN</span><h1>{{ section==='accounts'?'账号管理':section==='roles'?'游戏角色':'设备运行' }}</h1></div><div class="top-actions"><span class="connection">更新于 {{ refreshedAt || '—' }}</span><button class="secondary" :disabled="busy" @click="refresh">刷新</button><a class="secondary admin-link" href="/">用户工作台</a></div></header>
      <main><p v-if="error" class="message error" role="alert">{{ error }}</p><div class="stats admin-stats"><div class="stat m-surface"><small>用户账号</small><strong>{{ overview.accounts }}<span> / {{ overview.active_accounts }} 启用</span></strong></div><div class="stat m-surface"><small>在线从机</small><strong>{{ overview.online_devices }}<span> / {{ overview.devices }}</span></strong></div><div class="stat m-surface"><small>在线角色</small><strong>{{ overview.online_roles }}<span> / {{ overview.roles }}</span></strong></div></div>
        <section class="panel m-surface"><header class="panel-head admin-panel-head"><div><h2>{{ section==='accounts'?'全部账号':section==='roles'?'全部游戏角色':'全部从机' }}</h2><span class="panel-meta">共 {{ current.total }} 条</span></div><form class="admin-filters" @submit.prevent="offset=0;refresh()"><input v-model="search" :placeholder="section==='accounts'?'搜索账号或名称':section==='roles'?'搜索游戏 ID、区服、昵称或账号':'搜索设备名、编号或账号'" aria-label="搜索"><span v-if="accountFilter!==''" class="admin-filter-chip">账号 #{{ accountFilter }} <button type="button" aria-label="清除账号筛选" @click="accountFilter='';offset=0;refresh()">×</button></span><button class="primary small" type="submit">查询</button></form></header>
          <div v-if="!current.items.length" class="empty">暂无符合条件的记录</div>
          <div v-else class="table-wrap"><table v-if="section==='accounts'"><thead><tr><th>账号</th><th>状态</th><th>设备</th><th>角色</th><th>注册时间</th><th>最后登录</th><th>查看</th></tr></thead><tbody><tr v-for="item in accounts.items" :key="item.account_id"><td><strong>{{ item.display_name || item.username }}</strong><small>{{ item.username }} · #{{ item.account_id }}</small></td><td><span :class="['badge',item.disabled?'danger':'good']">{{ item.disabled?'已停用':'正常' }}</span><small v-if="item.is_admin">管理员</small></td><td>{{ item.device_count }}</td><td>{{ item.role_count }}</td><td>{{ time(item.created_at) }}</td><td>{{ time(item.last_login_at) }}</td><td class="actions"><button @click="viewAccount(item.account_id,'roles')">角色</button><button @click="viewAccount(item.account_id,'devices')">设备</button></td></tr></tbody></table>
            <table v-else-if="section==='roles'" class="admin-roles-table"><thead><tr><th>游戏角色</th><th>所属账号</th><th>状态</th><th>最近设备</th><th>背包空格</th><th>体力</th><th>游戏币</th><th>最近上报</th></tr></thead><tbody><tr v-for="item in roles.items" :key="item.character_id"><td><strong>{{ item.server_name || '未知区服' }} / {{ item.role_name || '未知角色' }}</strong><small>游戏 ID：{{ item.character_id }} · {{ item.stall_mode==='buy'?'收货':item.stall_mode==='sell'?'出货':'未知状态' }}<template v-if="item.stall_mode==='sell'"> · 在售 {{ item.sell_stock_count ?? '—' }} 件</template></small></td><td>{{ item.account_name || item.account_username }}<small>{{ item.account_username }}<template v-if="item.account_count>1"> · 曾关联 {{ item.account_count }} 个账号</template></small></td><td><span :class="['badge',item.role_online?'good':'danger']">{{ item.role_online?'在线':'离线' }}</span></td><td>{{ item.device_name || item.hostname || '—' }}</td><td>{{ item.empty_slots == null?'—':`${item.empty_slots} 格` }}</td><td>{{ stamina(item)==null?'未识别':`${stamina(item)}/${item.stamina_max}` }}<small v-if="stamina(item)!=null && item.stamina_full_at">{{ stamina(item)! >= item.stamina_max!?'已满':`预计满 ${time(item.stamina_full_at)}` }}</small></td><td>{{ money(item.cash) }}<small v-if="item.money_confirmed_at">{{ time(item.money_confirmed_at) }}</small></td><td>{{ time(item.last_seen_at) }}</td></tr></tbody></table>
            <table v-else><thead><tr><th>从机名称</th><th>所属账号</th><th>状态</th><th>客户端版本</th><th>监控窗口</th><th>最后连接</th></tr></thead><tbody><tr v-for="item in devices.items" :key="item.device_id"><td><strong>{{ item.display_name || item.hostname || item.device_id }}</strong><small>{{ item.device_id }}</small></td><td>{{ item.account_username }} · #{{ item.account_id }}</td><td><span :class="['badge',item.disabled?'danger':online(item.last_seen_at)?'good':'']">{{ item.disabled?'已禁用':online(item.last_seen_at)?'在线':'离线' }}</span></td><td>{{ item.app_version || '—' }}</td><td>{{ item.window_count }}</td><td>{{ time(item.last_seen_at) }}</td></tr></tbody></table></div>
          <div class="pagination"><span class="admin-page-info">{{ current.total ? offset+1 : 0 }}–{{ Math.min(offset+limit,current.total) }} / {{ current.total }}</span><button class="secondary" :disabled="offset===0 || busy" @click="changePage(Math.max(0,offset-limit))">上一页</button><button class="secondary" :disabled="offset+limit>=current.total || busy" @click="changePage(offset+limit)">下一页</button></div>
        </section>
      </main><footer>全站数据仅供管理员查看 · 当前版本为只读总览</footer>
    </div>
  </div>
</template>
