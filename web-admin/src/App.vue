<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import Panel from './components/Panel.vue'
import GemWorkspace from './components/GemWorkspace.vue'
import ProductStatistics from './components/ProductStatistics.vue'
import MyStalls from './components/MyStalls.vue'
import { api, json, online, time, TOKEN_KEY, USERNAME_KEY, Unauthorized } from './api'
import { estimateStamina } from './stamina'
import { sortRolesByServer } from './roleSort'
import alarmSoundUrl from './assets/inventory-alarm-loop-v1.wav'

type Page = 'monitor' | 'alerts' | 'devices' | 'logs' | 'my-stalls' | 'products' | 'merchant' | 'settings'
type RoleMode = 'buy' | 'sell' | 'unknown'
type WindowStatus = { window_id: string; server_name?: string; role_name?: string; character_id?: string; status?: string; empty_slots?: number; occupied_slots?: number; confidence?: number; occlusion_percent?: number; minimized?: boolean; monitoring?: boolean; cash?: number | null; money_confirmed_at?: string; money_readable?: boolean; updated_at?: string; screenshot_at?: string | null }
type Device = { device_id: string; display_name?: string; hostname?: string; note?: string; app_version?: string; last_seen_at?: string; disabled?: boolean; windows?: WindowStatus[] }
type RoleStatus = WindowStatus & { device_id?: string; device_name?: string; role_online: boolean; screenshot_id?: number; screenshot_count?: number; screenshot_device_id?: string; screenshot_window_id?: string; received_at?: string; first_seen_at?: string; stamina_current?: number | null; stamina_max?: number | null; character_level?: number | null; stamina_recovery_per_5m?: number | null; stamina_observed_at?: string | null; stamina_full_at?: string | null; stall_mode?: 'buy' | 'sell' | 'unknown'; sell_stock_count?: number | null; device: Device }
type RoleCard = RoleStatus & { stamina_estimate: number | null }
type Alert = { server_event_id: number; event_type: string; display_name?: string; hostname?: string; device_id?: string; server_name?: string; role_name?: string; occurred_at?: string; acked_at?: string | null; empty_slots?: number; confidence?: number; payload?: { details?: { cash?: number; threshold_wan?: number; source?: string; previous_count?: number; current_count?: number; sold_count?: number } } }
type Log = { id: number; event_type: string; panel_type?: string; message?: string; display_name?: string; device_id?: string; server_name?: string; role_name?: string; occurred_at?: string; reason?: string; target_status?: string; target_name?: string; target_level?: number; occlusion_percent?: number | null; minimized?: boolean | null; screenshot_ready?: boolean; screenshot_expires_at?: string }
type Bark = { enabled: boolean; call_enabled: boolean; repeat_minutes: number; local_alert_enabled: boolean; web_sound_enabled: boolean; capacity_threshold: number; cash_threshold_wan: number; capacity_enabled: boolean; cash_low_enabled: boolean; capacity_modes: RoleMode[]; cash_low_modes: RoleMode[]; stamina_full_enabled: boolean; stall_sold_enabled: boolean; configured?: boolean; masked_key?: string }
type Policy = { occlusion_enabled: boolean; occlusion_percent: number; occlusion_seconds: number }

const pages: { key: Page; label: string; hint: string }[] = [
  { key: 'monitor', label: '监控', hint: '角色状态与现金' },
  { key: 'alerts', label: '告警', hint: '待处理事件' },
  { key: 'devices', label: '从机管理', hint: '登录与设备' },
  { key: 'logs', label: '监控日志', hint: '操作与截图' },
  { key: 'my-stalls', label: '我的摊位', hint: '我的价格与同行对比' },
  { key: 'products', label: '商品统计', hint: '宝石与市场价格' },
  { key: 'merchant', label: '宝石商人', hint: '合成与利润测算' },
  { key: 'settings', label: '设置', hint: '上报与通知' },
]
const statusLabels: Record<string, string> = { full: '背包已满', almost_full: '接近满格', not_full: '背包未满', inventory_closed: '背包未打开', window_closed: '窗口已关闭', waiting: '等待检测', unknown: '未知状态' }
const alertLabels: Record<string, string> = { inventory_full: '背包已满', inventory_capacity: '背包容量不足', cash_low: '游戏币不足', stamina_full: '体力已满', stall_sold: '商品售出', inventory_closed: '背包未打开', inventory_occluded: '背包被遮挡', window_occluded: '窗口被遮挡', window_minimized: '窗口最小化' }
const panelLabels: Record<string, string> = { inventory: '背包', give: '给予面板', trade: '交易面板', attributes: '属性面板', stall: '卖货摊位', stall_buy: '收货摊位', stall_records: '摊位记录面板', stall_other_buy: '他人收货摊位' }
const reasons: Record<string, string> = { startup: '启动程序', resume: '点击开始', pause: '点击暂停', close: '关闭程序', recognition: '面板识别' }

const page = ref<Page>('monitor')
const monitorView = ref<'card' | 'list'>(localStorage.getItem('hoyo.monitorView') === 'list' ? 'list' : 'card')
const token = ref(localStorage.getItem(TOKEN_KEY) || '')
const username = ref(localStorage.getItem(USERNAME_KEY) || '')
const password = ref('')
const accountName = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const connected = ref(false)
const updatedAt = ref('')
const nowMs = ref(Date.now())
const devices = ref<Device[]>([])
const roles = ref<Omit<RoleStatus, 'device'>[]>([])
const alerts = ref<Alert[]>([])
const logs = ref<Log[]>([])
const logHasMore = ref(false)
const logCursor = ref(0)
const logNext = ref(0)
const filter = reactive({ event_type: '', panel_type: '' })
const policy = reactive<Policy>({ occlusion_enabled: true, occlusion_percent: 60, occlusion_seconds: 5 })
const bark = reactive<Bark>({ enabled: false, call_enabled: true, repeat_minutes: 10, local_alert_enabled: false, web_sound_enabled: false, capacity_threshold: 0, cash_threshold_wan: 500, capacity_enabled: true, cash_low_enabled: true, capacity_modes: ['buy'], cash_low_modes: ['buy'], stamina_full_enabled: false, stall_sold_enabled: false })
const barkKey = ref('')
const preview = ref('')
const previewError = ref('')
const previewTime = ref('')
type HistoricalShot = { id: number; device_id: string; window_id: string; captured_at: string; server_name?: string; role_name?: string }
const history = ref<HistoricalShot[]>([])
const historyIndex = ref(0)
const historyTotal = ref(0)
const historyCharacter = ref('')
const historyHasMore = ref(false)
const historyBusy = ref(false)
const snapshotInterval = ref(5)
const thumbnails = reactive<Record<string, string>>({})
const thumbnailDates = reactive<Record<string, string>>({})
const thumbnailExpiresAt = reactive<Record<string, number>>({})
const thumbnailRetryAt = reactive<Record<string, number>>({})
const thumbnailLoading = new Set<string>()
const pendingSnapshots = reactive<Record<string, boolean>>({})
const webSound = ref(false)
const soundReady = ref(false)
const ringing = ref(false)
let knownAlerts: Set<number> | undefined
let alarmAudio: HTMLAudioElement | undefined
let audioUnlockAttempted = false
let sampleTimer: number | undefined
let timer: number | undefined
let logStreamController: AbortController | undefined
let session = 0

const windows = computed<RoleCard[]>(() => roles.value.map(role => ({ ...role,
  device: devices.value.find(device => device.device_id === role.device_id) || { device_id: role.device_id || '' },
  stamina_estimate: estimateStamina(role, nowMs.value),
})))
const stallGroups = computed(() => [
  { key: 'buy', title: '收货', items: sortRolesByServer(windows.value.filter(item => item.stall_mode === 'buy')) },
  { key: 'sell', title: '出货', items: sortRolesByServer(windows.value.filter(item => item.stall_mode === 'sell')) },
  { key: 'unknown', title: '未知状态', items: sortRolesByServer(windows.value.filter(item => !item.stall_mode || item.stall_mode === 'unknown')) },
])
const snapshotKey = (item: RoleStatus) => item.character_id || `${item.device_id}:${item.window_id}`

async function snapshotUrl(deviceId: string, windowId: string) {
  return api<{ url: string; captured_at: string; expires_in: number }>(`/api/v1/master/live-screenshots/${encodeURIComponent(deviceId)}/view?window_id=${encodeURIComponent(windowId)}`)
}

async function refreshThumbnails() {
  if (page.value !== 'monitor' || monitorView.value !== 'card') return
  const now = Date.now()
  await Promise.all(windows.value.filter(item => item.screenshot_at && item.screenshot_device_id && item.screenshot_window_id &&
    !thumbnailLoading.has(snapshotKey(item)) && now >= (thumbnailRetryAt[snapshotKey(item)] || 0) &&
    (thumbnailDates[snapshotKey(item)] !== item.screenshot_at || now >= (thumbnailExpiresAt[snapshotKey(item)] || 0)))
    .map(async item => {
      const key = snapshotKey(item)
      thumbnailLoading.add(key)
      try {
        const shot = item.screenshot_id
          ? await api<{ url: string; captured_at: string; expires_in: number }>(`/api/v1/master/live-screenshots/history/${item.screenshot_id}/view`)
          : await snapshotUrl(item.screenshot_device_id!, item.screenshot_window_id!)
        if (new URL(shot.url).protocol !== 'https:') return
        thumbnails[key] = shot.url
        thumbnailDates[key] = item.screenshot_at || ''
        thumbnailExpiresAt[key] = Date.now() + Math.max(30, (shot.expires_in || 300) - 60) * 1000
      } catch { thumbnailRetryAt[key] = Date.now() + 15_000 }
      finally { thumbnailLoading.delete(key) }
    }))
}

function thumbnailError(item: RoleStatus) {
  const key = snapshotKey(item)
  delete thumbnails[key]
  delete thumbnailDates[key]
  delete thumbnailExpiresAt[key]
  thumbnailRetryAt[key] = Date.now() + 15_000
}

async function viewSnapshot(deviceId: string, windowId: string) {
  history.value = []; historyCharacter.value = ''; historyHasMore.value = false; historyTotal.value = 0
  preview.value = ''; previewError.value = ''
  const shot = await snapshotUrl(deviceId, windowId)
  if (new URL(shot.url).protocol !== 'https:') throw new Error('截图地址无效')
  previewTime.value = time(shot.captured_at)
  preview.value = shot.url
}

async function openHistory(item: RoleStatus) {
  if (!item.character_id) { await viewSnapshot(item.screenshot_device_id || item.device.device_id, item.screenshot_window_id || item.window_id); return }
  preview.value = ''; previewError.value = ''
  historyCharacter.value = item.character_id
  const result = await api<{ items: HistoricalShot[]; has_more: boolean; total: number }>(`/api/v1/master/live-screenshots/history?character_id=${encodeURIComponent(item.character_id)}&limit=30`)
  history.value = result.items
  historyHasMore.value = result.has_more
  historyTotal.value = result.total
  historyIndex.value = 0
  if (history.value.length) await showHistorical(0)
  else if (item.screenshot_device_id && item.screenshot_window_id) await viewSnapshot(item.screenshot_device_id, item.screenshot_window_id)
}

async function showHistorical(index: number) {
  const entry = history.value[index]
  if (!entry) return
  historyBusy.value = true
  try {
    const shot = await api<{ url: string; captured_at: string }>(`/api/v1/master/live-screenshots/history/${entry.id}/view`)
    if (new URL(shot.url).protocol !== 'https:') throw new Error('截图地址无效')
    historyIndex.value = index
    previewTime.value = time(shot.captured_at)
    preview.value = shot.url
    previewError.value = ''
  } finally { historyBusy.value = false }
}

async function navigateHistory(direction: number) {
  if (historyBusy.value || !historyCharacter.value) return
  const next = historyIndex.value + direction
  if (next < 0) return
  if (next >= history.value.length && historyHasMore.value) {
    const before = history.value.at(-1)?.id
    const result = await api<{ items: HistoricalShot[]; has_more: boolean; total: number }>(`/api/v1/master/live-screenshots/history?character_id=${encodeURIComponent(historyCharacter.value)}&before=${before}&limit=30`)
    history.value.push(...result.items)
    historyHasMore.value = result.has_more
    historyTotal.value = result.total
  }
  if (next < history.value.length) await showHistorical(next)
}

function previewKey(event: KeyboardEvent) {
  if (!preview.value) return
  if (event.key === 'Escape') preview.value = ''
  if (event.key === 'ArrowLeft') void navigateHistory(-1).catch(fail)
  if (event.key === 'ArrowRight') void navigateHistory(1).catch(fail)
}

async function requestSnapshot(item: RoleStatus) {
  const key = snapshotKey(item)
  pendingSnapshots[key] = true
  try {
    const result = await api<{ request_id: string }>(`/api/v1/master/live-screenshots/${encodeURIComponent(item.device.device_id)}/request`, json('POST', { window_id: item.window_id }))
    for (let attempt = 0; attempt < 24; attempt++) {
      await new Promise(resolve => window.setTimeout(resolve, 2000))
      const state = await api<{ state: string; error?: string }>(`/api/v1/master/live-screenshots/requests/${encodeURIComponent(result.request_id)}`)
      if (state.state === 'complete') {
        await openHistory(item)
        await refresh()
        return
      }
      if (state.state !== 'pending') throw new Error(state.error || '实时截图超时，请检查从机和游戏窗口')
    }
    throw new Error('实时截图超时，请稍后重试')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { delete pendingSnapshots[key] }
}
const pending = computed(() => alerts.value.filter(item => !item.acked_at).length)
const activeDevices = computed(() => devices.value.filter(item => !item.disabled && online(item.last_seen_at)).length)
const eventTypes = [
  ['', '全部类型'], ['window_occluded', '遮挡'], ['window_minimized', '最小化'],
  ['monitoring_started', '开始监控'], ['monitoring_paused', '暂停监控'], ['window_monitored', '开始监控角色'],
  ['panel_opened', '打开面板'], ['panel_closed', '关闭面板'],
  ['panel_target_changed', '对象变化'], ['market_stall_changed', '切换他人摊位'], ['game_default', '默认界面'],
]

function fail(cause: unknown) {
  if (cause instanceof Unauthorized) { logout('登录已失效，请重新登录'); return }
  error.value = cause instanceof Error ? cause.message : String(cause)
  connected.value = false
}

function logout(message = '') {
  ++session
  stopLogStream()
  localStorage.removeItem(TOKEN_KEY)
  token.value = ''
  password.value = ''
  devices.value = []
  alerts.value = []
  logs.value = []
  preview.value = ''
  error.value = message
  connected.value = false
  stopSound()
  knownAlerts = undefined
  if (timer) window.clearInterval(timer)
  timer = undefined
}

function alarmPlayer(): HTMLAudioElement {
  alarmAudio ||= new Audio(alarmSoundUrl)
  alarmAudio.loop = true
  alarmAudio.volume = 1
  return alarmAudio
}

function stopSound() {
  ringing.value = false
  if (sampleTimer) window.clearTimeout(sampleTimer)
  sampleTimer = undefined
  alarmAudio?.pause()
  if (alarmAudio) alarmAudio.currentTime = 0
}

async function unlockWebSound(playSample = true) {
  audioUnlockAttempted = true
  try {
    const player = alarmPlayer()
    await player.play()
    soundReady.value = true
    if (playSample) {
      notice.value = '当前浏览器已允许播放从机告警音'
      if (sampleTimer) window.clearTimeout(sampleTimer)
      sampleTimer = window.setTimeout(() => {
        sampleTimer = undefined
        if (!ringing.value) { player.pause(); player.currentTime = 0 }
      }, 1900)
    } else if (!ringing.value) { player.pause(); player.currentTime = 0 }
  } catch { soundReady.value = false; error.value = '浏览器未允许播放声音，请点击页面后重试' }
}

function onAudioGesture() {
  if (webSound.value && !soundReady.value && !audioUnlockAttempted) void unlockWebSound(false)
}

async function login() {
  busy.value = true; error.value = ''
  try {
    const response = await api<{ access_token: string; account: { display_name?: string } }>('/api/v1/auth/login', json('POST', { username: username.value.trim(), password: password.value }))
    localStorage.setItem(TOKEN_KEY, response.access_token)
    localStorage.setItem(USERNAME_KEY, username.value.trim())
    token.value = response.access_token
    accountName.value = response.account.display_name || username.value
    password.value = ''
    start()
  } catch (cause) { fail(cause) }
  finally { busy.value = false }
}

async function refresh() {
  nowMs.value = Date.now()
  if (!token.value || loading.value) return
  loading.value = true
  const current = session
  try {
    const [status, events, pendingEvents, accountAlerts] = await Promise.all([
      api<{ devices: Device[]; roles: Omit<RoleStatus, 'device'>[] }>('/api/v1/master/status'),
      api<{ events: Alert[] }>('/api/v1/master/events?state=all&limit=200&latest=true'),
      api<{ events: Alert[] }>('/api/v1/master/events?state=pending&limit=500&latest=true'),
      api<Bark>('/api/v1/master/notifications/bark'),
    ])
    if (current !== session) return
    devices.value = status.devices || []
    roles.value = status.roles || []
    webSound.value = accountAlerts.web_sound_enabled
    if (!webSound.value) stopSound()
    if (page.value !== 'settings') Object.assign(bark, accountAlerts)
    if (page.value === 'monitor' && monitorView.value === 'card') void refreshThumbnails()
    alerts.value = Array.from(new Map([...(events.events || []), ...(pendingEvents.events || [])]
      .map(item => [item.server_event_id, item])).values()).sort((a, b) => b.server_event_id - a.server_event_id)
    const nextAlerts = new Set(alerts.value.filter(item => !item.acked_at).map(item => item.server_event_id))
    if (knownAlerts && webSound.value && soundReady.value && !ringing.value && [...nextAlerts].some(id => !knownAlerts?.has(id))) {
      ringing.value = true
      void alarmPlayer().play().catch(() => { ringing.value = false; soundReady.value = false })
    }
    knownAlerts = nextAlerts
    if (!nextAlerts.size) stopSound()
    updatedAt.value = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
    connected.value = true; error.value = ''
    if (page.value === 'logs' && !logStreamController) await fetchLogs(logCursor.value)
  } catch (cause) { if (current === session) fail(cause) }
  finally { loading.value = false }
}

async function loadSettings() {
  try {
    const [storedPolicy, storedBark, account, liveSettings] = await Promise.all([
      api<Policy>('/api/v1/master/monitor-log-settings'),
      api<Bark>('/api/v1/master/notifications/bark'),
      api<{ account: { display_name?: string; username?: string } }>('/api/v1/master/account'),
      api<{ interval_minutes: number }>('/api/v1/master/live-screenshot-settings'),
    ])
    Object.assign(policy, storedPolicy)
    Object.assign(bark, storedBark)
    webSound.value = storedBark.web_sound_enabled
    if (!webSound.value) stopSound()
    else if (alarmAudio && !alarmAudio.paused) soundReady.value = true
    snapshotInterval.value = liveSettings.interval_minutes
    accountName.value = account.account?.display_name || account.account?.username || username.value
  } catch (cause) { fail(cause) }
}

function start() {
  ++session
  if (timer) window.clearInterval(timer)
  void refresh()
  void loadSettings()
  timer = window.setInterval(() => void refresh(), 5000)
}

function choose(next: Page) {
  if (next !== 'logs') stopLogStream()
  page.value = next
  notice.value = ''
  if (next === 'monitor' && monitorView.value === 'card') void refreshThumbnails()
  if (next === 'logs') void fetchLogs(0)
  if (next === 'settings') void loadSettings()
}

function setMonitorView(next: 'card' | 'list') {
  monitorView.value = next
  localStorage.setItem('hoyo.monitorView', next)
  if (next === 'card') void refreshThumbnails()
}

async function fetchLogs(cursor: number) {
  if (!token.value) return
  if (cursor > 0) stopLogStream()
  if (cursor > 0) stopLogStream()
  const params = new URLSearchParams({ limit: '100', before_id: String(cursor) })
  if (filter.event_type) params.set('event_type', filter.event_type)
  if (filter.panel_type) params.set('panel_type', filter.panel_type)
  const current = session
  try {
    const data = await api<{ logs: Log[]; has_more: boolean }>(`/api/v1/master/monitor-logs?${params}`)
    if (current !== session) return
    logs.value = data.logs || []
    logCursor.value = cursor
    logNext.value = logs.value.at(-1)?.id || 0
    logHasMore.value = data.has_more
    if (cursor === 0) void startLogStream()
    if (cursor === 0 && page.value === 'logs') void startLogStream()
  } catch (cause) { if (current === session) fail(cause) }
}

function stopLogStream() {
  logStreamController?.abort()
  logStreamController = undefined
}

async function startLogStream() {
  if (!token.value || page.value !== 'logs' || logCursor.value !== 0) return
  stopLogStream()
  const controller = new AbortController()
  logStreamController = controller
  const current = session
  const params = new URLSearchParams()
  if (filter.event_type) params.set('event_type', filter.event_type)
  if (filter.panel_type) params.set('panel_type', filter.panel_type)
  try {
    const response = await fetch(`/api/v1/master/monitor-logs/stream?${params}`, {
      headers: { Authorization: `Bearer ${token.value}` }, cache: 'no-store', signal: controller.signal,
    })
    if (response.status === 401) throw new Unauthorized('登录已失效')
    if (!response.ok || !response.body) throw new Error(`实时日志连接失败 (${response.status})`)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (!controller.signal.aborted) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const line = block.split('\n').find(item => item.startsWith('data: '))
        if (line && current === session && page.value === 'logs' && logCursor.value === 0) {
          const data = JSON.parse(line.slice(6)) as { logs: Log[]; has_more: boolean }
          logs.value = data.logs || []
          logNext.value = logs.value.at(-1)?.id || 0
          logHasMore.value = data.has_more
          connected.value = true
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
    if (!controller.signal.aborted && current === session) {
      window.setTimeout(() => { if (page.value === 'logs') void startLogStream() }, 1000)
    }
  } catch (cause) {
    if (!controller.signal.aborted && current === session) {
      if (cause instanceof Unauthorized) fail(cause)
      else window.setTimeout(() => { if (page.value === 'logs') void startLogStream() }, 1000)
    }
  } finally {
    if (logStreamController === controller) logStreamController = undefined
  }
}

function logLabel(item: Log): string {
  const base = item.event_type === 'game_default' ? '关闭面板，返回默认游戏界面'
    : item.event_type === 'panel_opened' ? `打开${panelLabels[item.panel_type || ''] || '面板'}`
    : item.event_type === 'panel_closed' ? `关闭${panelLabels[item.panel_type || ''] || '面板'}`
    : item.event_type === 'panel_target_changed' ? `${item.panel_type === 'give' ? '给予' : '交易'}对象变化`
    : item.event_type === 'market_stall_changed' ? '切换浏览其他人摊位'
    : ({ window_occluded: '遮挡', window_minimized: '最小化', monitoring_started: '开始监控', monitoring_paused: '暂停监控', window_monitored: '开始监控角色' } as Record<string, string>)[item.event_type] || item.event_type
  if (['give', 'trade'].includes(item.panel_type || '') && item.target_status != null) {
    const target = item.target_status === 'recognized' && item.target_name && item.target_level ? `${item.target_name}，${item.target_level}级` : '对象未识别'
    return `${base}，对象：${target}`
  }
  return item.message || base
}

function alertLabel(item: Alert): string {
  if (item.event_type === 'stamina_full') return `体力已满（${item.payload?.details?.source === 'measured' ? '实测' : '预测'}）`
  if (item.event_type === 'stall_sold') {
    const details = item.payload?.details
    return details?.previous_count != null && details.current_count != null
      ? `商品售出：在售 ${details.previous_count}→${details.current_count} 件，减少 ${details.sold_count ?? '—'} 件`
      : '商品售出'
  }
  if (item.event_type !== 'cash_low') return alertLabels[item.event_type] || item.event_type
  const cash = item.payload?.details?.cash
  const threshold = item.payload?.details?.threshold_wan
  return cash != null && threshold != null
    ? `游戏币不足：当前 ${Number(cash).toLocaleString('zh-CN')}，低于 ${threshold} 万`
    : '游戏币不足'
}

async function screenshot(item: Log) {
  preview.value = ''; previewError.value = ''; previewTime.value = ''
  try {
    const result = await api<{ url: string }>(`/api/v1/master/monitor-logs/${item.id}/screenshot`)
    const link = new URL(result.url)
    if (link.protocol !== 'https:') throw new Error('截图地址无效')
    preview.value = link.href
  } catch (cause) { fail(cause) }
}

async function ack(item: Alert) {
  try { await api(`/api/v1/master/events/${item.server_event_id}/ack`, json('POST', {})); await refresh() }
  catch (cause) { fail(cause) }
}

async function editDevice(device: Device) {
  const name = window.prompt('从机名称', device.display_name || device.hostname || '')
  if (name === null) return
  const cleaned = name.trim()
  if (!cleaned || cleaned.length > 64) { error.value = '名称应为1至64个字符'; return }
  try { await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}`, json('PATCH', { display_name: cleaned })); await refresh() }
  catch (cause) { fail(cause) }
}

async function toggleDevice(device: Device) {
  try { await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}`, json('PATCH', { disabled: !device.disabled })); await refresh() }
  catch (cause) { fail(cause) }
}

async function removeDevice(device: Device) {
  if (!window.confirm(`确定移除“${device.display_name || device.device_id}”吗？`)) return
  try { await api(`/api/v1/master/devices/${encodeURIComponent(device.device_id)}`, { method: 'DELETE' }); await refresh() }
  catch (cause) { fail(cause) }
}

async function savePolicy() {
  busy.value = true
  try { Object.assign(policy, await api<Policy>('/api/v1/master/monitor-log-settings', json('PUT', { ...policy }))); notice.value = '遮挡日志规则已保存' }
  catch (cause) { fail(cause) }
  finally { busy.value = false }
}

async function saveSnapshotInterval() {
  busy.value = true
  try {
    const result = await api<{ interval_minutes: number }>('/api/v1/master/live-screenshot-settings', json('PUT', { interval_minutes: snapshotInterval.value }))
    snapshotInterval.value = result.interval_minutes
    notice.value = `截图周期已设为 ${result.interval_minutes} 分钟`
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
  finally { busy.value = false }
}

async function saveBark() {
  busy.value = true
  try {
    if (bark.capacity_enabled && !bark.capacity_modes.length) throw new Error('请为背包空格告警至少选择一种角色状态')
    if (bark.cash_low_enabled && !bark.cash_low_modes.length) throw new Error('请为游戏币告警至少选择一种角色状态')
    if (bark.web_sound_enabled && !soundReady.value) await unlockWebSound()
    let key = barkKey.value.trim()
    if (/^https?:\/\//i.test(key)) {
      const link = new URL(key)
      if (link.hostname !== 'api.day.app') throw new Error('请使用 Bark 官方测试地址')
      key = link.pathname.split('/').filter(Boolean)[0] || ''
    }
    const data = { enabled: bark.enabled, call_enabled: bark.call_enabled, repeat_minutes: bark.repeat_minutes,
      local_alert_enabled: bark.local_alert_enabled, web_sound_enabled: bark.web_sound_enabled,
      capacity_threshold: bark.capacity_threshold, cash_threshold_wan: bark.cash_threshold_wan,
      capacity_enabled: bark.capacity_enabled, cash_low_enabled: bark.cash_low_enabled,
      capacity_modes: bark.capacity_modes, cash_low_modes: bark.cash_low_modes,
      stamina_full_enabled: bark.stamina_full_enabled,
      stall_sold_enabled: bark.stall_sold_enabled,
      ...(key ? { device_key: key } : {}) }
    Object.assign(bark, await api<Bark>('/api/v1/master/notifications/bark', json('PUT', data)))
    webSound.value = bark.web_sound_enabled
    if (!webSound.value) stopSound()
    barkKey.value = ''; notice.value = '账号告警设置已保存，从机将在约 30 秒内同步'
  } catch (cause) { fail(cause) }
  finally { busy.value = false }
}

async function testBark() {
  try { await api('/api/v1/master/notifications/bark/test', json('POST', {})); notice.value = '测试通知已发送，请查看手机' }
  catch (cause) { fail(cause) }
}

watch([() => filter.event_type, () => filter.panel_type], () => { if (page.value === 'logs') { stopLogStream(); void fetchLogs(0) } })
onMounted(() => { window.addEventListener('keydown', previewKey); window.addEventListener('pointerdown', onAudioGesture); if (token.value) start() })
onUnmounted(() => { stopLogStream(); window.removeEventListener('keydown', previewKey); window.removeEventListener('pointerdown', onAudioGesture); if (timer) window.clearInterval(timer); stopSound() })
</script>

<template>
  <div v-if="!token" class="login-stage">
    <section class="login-card m-surface">
      <span class="eyebrow">HOYO MONITOR · WEB</span>
      <h1>梦幻西游监控后台</h1>
      <p>登录账号，查看已登录从机的监控状态。</p>
      <form @submit.prevent="login">
        <label>账号<input v-model="username" autocomplete="username" required minlength="3" maxlength="32"></label>
        <label>密码<input v-model="password" type="password" autocomplete="current-password" required minlength="8"></label>
        <button class="primary" :disabled="busy" type="submit">{{ busy ? '登录中…' : '登录' }}</button>
      </form>
      <p v-if="error" class="message error" role="alert">{{ error }}</p>
    </section>
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar m-rail">
      <div class="brand"><span class="brand-mark">M</span><div><strong>梦幻西游监控</strong><small>MONITOR CONSOLE</small></div></div>
      <nav aria-label="管理板块">
        <button v-for="item in pages" :key="item.key" :class="['nav-item', { active: page === item.key }]" @click="choose(item.key)">
          <strong>{{ item.label }}</strong><small>{{ item.hint }}</small><em v-if="item.key === 'alerts' && pending">{{ pending }}</em>
        </button>
      </nav>
      <div class="sidebar-foot"><span>{{ accountName || username }}</span><button @click="logout()">退出登录</button></div>
    </aside>
    <div class="workspace">
      <header class="topbar"><div><span class="eyebrow">HOYO MONITOR / {{ pages.find(item => item.key === page)?.label }}</span><h1>{{ pages.find(item => item.key === page)?.label }}</h1></div><div class="top-actions"><button v-if="ringing" class="primary" @click="stopSound">停止提示音</button><span :class="['connection', { offline: !connected }]">{{ connected ? '云端已连接' : '连接中断' }}</span><button class="secondary" :disabled="loading" @click="refresh">刷新</button></div></header>
      <main>
        <p v-if="error" class="message error" role="alert">{{ error }}</p>
        <p v-if="notice" class="message success" role="status">{{ notice }}</p>

        <template v-if="page === 'monitor'">
          <div class="stats"><div class="stat m-surface"><small>在线从机</small><strong>{{ activeDevices }} <span>/ {{ devices.length }}</span></strong></div><div class="stat m-surface"><small>监控窗口</small><strong>{{ windows.length }}</strong></div><div class="stat m-surface"><small>待处理告警</small><strong>{{ pending }}</strong></div><div class="stat m-surface"><small>统一更新时间</small><strong class="time-value">{{ updatedAt || '—' }}</strong></div></div>
          <div class="monitor-view-bar"><div class="monitor-view-switch" role="group" aria-label="监控展示方式"><button type="button" :class="{ active: monitorView === 'card' }" :aria-pressed="monitorView === 'card'" @click="setMonitorView('card')">卡片</button><button type="button" :class="{ active: monitorView === 'list' }" :aria-pressed="monitorView === 'list'" @click="setMonitorView('list')">列表</button></div></div>
          <div v-if="!windows.length" class="empty">暂无监控窗口，请在从机程序中添加游戏窗口。</div>
          <Panel v-for="group in stallGroups" :key="group.key" :title="group.title" :meta="monitorView === 'card' ? `${group.items.length} 个角色 · 截图每 ${snapshotInterval} 分钟` : `${group.items.length} 个角色`">
            <div v-if="!group.items.length" class="empty">暂无{{ group.title }}角色</div>
            <div v-else-if="monitorView === 'list'" class="table-wrap monitor-list-wrap">
              <table class="monitor-list"><thead><tr><th>区服 / 游戏昵称</th><th>监控状态</th><th>背包空格</th><th>体力</th><th>游戏币</th><th v-if="group.key === 'sell'">在售商品</th></tr></thead><tbody>
                <tr v-for="item in group.items" :key="snapshotKey(item)" :class="{ 'monitor-row-offline': !item.role_online }">
                  <td><strong>{{ item.server_name || '未知区服' }} / {{ item.role_name || '未知角色' }}</strong><small v-if="item.character_id">账号 ID：{{ item.character_id }}</small></td>
                  <td><span :class="['badge', !item.role_online || item.minimized ? 'danger' : item.monitoring ? 'good' : '']">{{ !item.role_online ? '已掉线' : item.minimized ? '已最小化' : item.monitoring ? '监控中' : '已暂停' }}</span></td>
                  <td>{{ item.empty_slots == null ? '—' : `${item.empty_slots} 格` }}<small>{{ statusLabels[item.status || 'unknown'] || item.status }}</small></td>
                  <td v-if="item.stamina_estimate != null && item.stamina_max != null">{{ item.stamina_estimate }}/{{ item.stamina_max }}<small :class="{ 'monitor-full': item.stamina_estimate >= item.stamina_max }">{{ item.stamina_estimate >= item.stamina_max ? '体力已满（预测）' : `预计满体力 ${time(item.stamina_full_at)}` }}</small></td><td v-else>未识别</td>
                  <td>{{ item.cash == null ? '未识别' : Number(item.cash).toLocaleString('zh-CN') }}<small v-if="item.money_confirmed_at">确认于 {{ time(item.money_confirmed_at) }}</small></td>
                  <td v-if="group.key === 'sell'">{{ item.sell_stock_count == null ? '待识别' : `${item.sell_stock_count} 件` }}</td>
                </tr>
              </tbody></table>
            </div>
            <div v-else class="snapshot-grid">
              <article v-for="item in group.items" :key="snapshotKey(item)" class="snapshot-card">
                <div class="snapshot-identity" :title="`${item.server_name || '未知区服'} / ${item.role_name || '未知角色'} · ${item.device_name || item.device.device_id}`">
                  <strong>{{ item.server_name || '未知区服' }} / {{ item.role_name || '未知角色' }}</strong>
                  <span>· {{ item.device_name || item.device.display_name || item.device.hostname || item.device.device_id }}</span>
                </div>
                <div class="snapshot-toolbar">
                  <span :class="['badge', !item.role_online || item.minimized ? 'danger' : item.monitoring ? 'good' : '']">{{ !item.role_online ? '已掉线' : item.minimized ? '已最小化' : item.monitoring ? '监控中' : '已暂停' }}</span>
                  <span class="snapshot-occlusion">遮挡 {{ item.occlusion_percent == null ? '—' : `${Number(item.occlusion_percent).toFixed(1)}%` }}</span>
                  <button class="primary small" :disabled="!!pendingSnapshots[snapshotKey(item)] || !item.role_online || !item.monitoring || item.minimized" @click="requestSnapshot(item)">{{ pendingSnapshots[snapshotKey(item)] ? '获取中…' : '获取实时截图' }}</button>
                </div>
                <button :class="['snapshot-image', { 'offline-shot': !item.role_online }]" :disabled="!item.screenshot_at" @click="openHistory(item).catch(fail)"><img v-if="thumbnails[snapshotKey(item)]" :src="thumbnails[snapshotKey(item)]" alt="游戏窗口最近截图，点击浏览该账号历史画面" referrerpolicy="no-referrer" @error="thumbnailError(item)"><span v-else>截图加载中…</span></button>
                <div class="snapshot-meta"><span>截图于 {{ time(item.screenshot_at) }}</span><button class="snapshot-count" :disabled="!item.screenshot_count" :aria-label="`查看${item.role_name || '角色'}的 ${item.screenshot_count || 0} 张历史截图`" @click="openHistory(item).catch(fail)">共 {{ item.screenshot_count || 0 }} 张</button><span v-if="item.screenshot_at && Date.now() - Date.parse(item.screenshot_at) > snapshotInterval * 120000" class="stale">画面可能已过时</span></div>
                <div class="snapshot-details"><span>{{ statusLabels[item.status || 'unknown'] || item.status }} · 空格 {{ item.empty_slots ?? '—' }}</span><span>现金 {{ item.cash == null ? '未识别' : Number(item.cash).toLocaleString('zh-CN') }}<small>{{ time(item.money_confirmed_at) }}</small></span></div>
                <div v-if="item.stall_mode === 'sell'" class="snapshot-details"><span>在售商品 {{ item.sell_stock_count == null ? '待识别' : `${item.sell_stock_count} 件` }}</span><small>卖货摊位第一页</small></div>
                <div v-if="item.stamina_estimate != null && item.stamina_max != null && item.stamina_full_at" class="stamina-summary">
                  <span>预计体力 {{ item.stamina_estimate }}/{{ item.stamina_max }} <small>上次实测 {{ item.stamina_current }}/{{ item.stamina_max }} · 观测于 {{ time(item.stamina_observed_at) }}<template v-if="!item.role_online"> · 离线后暂停估算，需复核</template></small></span>
                  <strong :class="{ full: item.stamina_estimate >= item.stamina_max }">{{ item.stamina_estimate >= item.stamina_max ? '体力已满（预测）' : `预计满体力：${time(item.stamina_full_at)}` }}</strong>
                </div>
              </article>
            </div>
          </Panel>
        </template>

        <template v-else-if="page === 'alerts'"><Panel title="告警记录" :meta="`待处理 ${pending} 条`"><div v-if="!alerts.length" class="empty">暂无告警记录</div><div v-else class="table-wrap"><table><thead><tr><th>状态</th><th>从机 / 角色</th><th>告警</th><th>发生时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in alerts" :key="item.server_event_id"><td><span :class="['badge', item.acked_at ? 'good' : 'danger']">{{ item.acked_at ? '已处理' : '未处理' }}</span></td><td>{{ item.display_name || item.hostname || item.device_id }}<small>{{ item.server_name || '—' }} / {{ item.role_name || '—' }}</small></td><td>{{ alertLabel(item) }}</td><td>{{ time(item.occurred_at) }}</td><td><button v-if="!item.acked_at" class="primary small" @click="ack(item)">确认处理</button><span v-else>—</span></td></tr></tbody></table></div></Panel></template>

        <template v-else-if="page === 'devices'"><Panel title="从机管理" :meta="`${devices.length} 台`"><div v-if="!devices.length" class="empty">暂无从机。请在从机程序中登录本账号，机器会自动出现在这里。</div><div v-else class="table-wrap"><table><thead><tr><th>从机名称</th><th>状态</th><th>版本</th><th>最后连接</th><th>操作</th></tr></thead><tbody><tr v-for="device in devices" :key="device.device_id"><td><strong>{{ device.display_name || device.hostname || device.device_id }}</strong><small v-if="device.note">{{ device.note }}</small></td><td><span :class="['badge', !device.disabled && online(device.last_seen_at) ? 'good' : '']">{{ device.disabled ? '已禁用' : online(device.last_seen_at) ? '在线' : '离线' }}</span></td><td>{{ device.app_version || '—' }}</td><td>{{ time(device.last_seen_at) }}</td><td class="actions"><button @click="editDevice(device)">编辑</button><button @click="toggleDevice(device)">{{ device.disabled ? '启用' : '禁用' }}</button><button class="text-danger" @click="removeDevice(device)">移除</button></td></tr></tbody></table></div></Panel></template>

        <template v-else-if="page === 'logs'"><Panel title="监控日志" :meta="`${logs.length} 条`"><template #tools><div class="filters"><select v-model="filter.event_type" aria-label="日志类型"><option v-for="[value, label] in eventTypes" :key="value" :value="value">{{ label }}</option></select><select v-model="filter.panel_type" aria-label="面板类型"><option value="">全部面板</option><option v-for="(name, key) in panelLabels" :key="key" :value="key">{{ name }}</option></select><button class="secondary" @click="fetchLogs(0)">最新</button></div></template><div v-if="!logs.length" class="empty">暂无符合条件的记录</div><div v-else class="table-wrap"><table><thead><tr><th>发生时间</th><th>从机 / 角色</th><th>类型与内容</th><th>遮挡 / 最小化</th><th>截图</th></tr></thead><tbody><tr v-for="item in logs" :key="item.id"><td>{{ time(item.occurred_at) }}</td><td>{{ item.display_name || item.device_id }}<small>{{ item.server_name || '—' }} / {{ item.role_name || '—' }}</small></td><td>{{ logLabel(item) }}<small>{{ reasons[item.reason || ''] || '状态上报' }}</small></td><td>{{ item.occlusion_percent == null ? '—' : `${Number(item.occlusion_percent).toFixed(1)}%` }} / {{ item.minimized == null ? '—' : item.minimized ? '是' : '否' }}</td><td><button v-if="item.screenshot_ready && Date.parse(item.screenshot_expires_at || '') > Date.now()" class="secondary small" @click="screenshot(item)">查看截图</button><span v-else>{{ item.screenshot_ready ? '已过期' : '—' }}</span></td></tr></tbody></table></div><div class="pagination"><button class="secondary" :disabled="logCursor === 0" @click="fetchLogs(0)">返回最新</button><button class="secondary" :disabled="!logHasMore" @click="fetchLogs(logNext)">更早记录</button></div></Panel></template>

        <template v-else-if="page === 'merchant'"><GemWorkspace mode="merchant" /></template>
        <template v-else-if="page === 'my-stalls'"><MyStalls /></template>
        <template v-else-if="page === 'products'"><ProductStatistics /></template>
        <template v-else><div class="settings-grid"><GemWorkspace mode="zones" /><Panel title="窗口截图" meta="账号级设置"><form class="form-grid" @submit.prevent="saveSnapshotInterval"><label>自动截图周期（分钟）<input v-model.number="snapshotInterval" type="number" min="1" max="60" step="1" required></label><p class="hint">可设置 1～60 分钟，默认 5 分钟。每个窗口的“获取实时截图”按钮不受周期限制。</p><button class="primary" :disabled="busy" type="submit">保存截图周期</button></form></Panel><Panel title="遮挡日志上报" meta="账号级设置"><form class="form-grid" @submit.prevent="savePolicy"><label class="check"><input v-model="policy.occlusion_enabled" type="checkbox"> 上报遮挡日志</label><label>遮挡比例达到（%）<input v-model.number="policy.occlusion_percent" type="number" min="0" max="100" step="0.1" required></label><label>连续检测时间（秒）<input v-model.number="policy.occlusion_seconds" type="number" min="0" max="3600" step="1" required></label><p class="hint">窗口状态仍正常同步；这里仅控制遮挡类型监控日志。</p><button class="primary" :disabled="busy" type="submit">保存上报规则</button></form></Panel>
          <Panel title="告警方式" meta="账号级设置，所有从机共用"><form class="form-grid" @submit.prevent="saveBark">
            <label class="check"><input v-model="bark.enabled" type="checkbox"> 手机是否推送</label>
            <label class="check"><input v-model="bark.local_alert_enabled" type="checkbox"> 当前设备是否报警</label>
            <label class="check"><input v-model="bark.web_sound_enabled" type="checkbox"> 电脑网页提示音</label>
            <div class="alert-rule"><label class="check"><input v-model="bark.capacity_enabled" type="checkbox"> 背包剩余空格告警</label><label>剩余空格不超过<input v-model.number="bark.capacity_threshold" type="number" min="0" max="20" step="1" required></label><div class="role-mode-choices" aria-label="背包空格告警适用角色状态"><span>适用状态</span><label class="check"><input v-model="bark.capacity_modes" type="checkbox" value="buy"> 收货</label><label class="check"><input v-model="bark.capacity_modes" type="checkbox" value="sell"> 出货</label><label class="check"><input v-model="bark.capacity_modes" type="checkbox" value="unknown"> 未知</label></div></div>
            <div class="alert-rule"><label class="check"><input v-model="bark.cash_low_enabled" type="checkbox"> 游戏币低于告警</label><label>低于（万）<input v-model.number="bark.cash_threshold_wan" type="number" min="1" max="99999999" step="1" required></label><div class="role-mode-choices" aria-label="游戏币告警适用角色状态"><span>适用状态</span><label class="check"><input v-model="bark.cash_low_modes" type="checkbox" value="buy"> 收货</label><label class="check"><input v-model="bark.cash_low_modes" type="checkbox" value="sell"> 出货</label><label class="check"><input v-model="bark.cash_low_modes" type="checkbox" value="unknown"> 未知</label></div></div>
            <label class="check"><input v-model="bark.stamina_full_enabled" type="checkbox"> 体力满提醒（实测已满或持续监控下预测满）</label>
            <label class="check"><input v-model="bark.stall_sold_enabled" type="checkbox"> 商品售出提醒（已确认在售数量减少）</label>
            <p class="hint">两类阈值告警默认只对收货角色生效；关闭类型或未选中角色状态时不会产生该告警。上方三个开关控制已产生告警的送达方式。网页提示音需要保持网页打开，并允许当前浏览器播放声音。</p>
            <label class="check"><input v-model="bark.call_enabled" type="checkbox"> 手机连续呼叫</label>
            <label>手机重复提醒间隔（分钟）<select v-model.number="bark.repeat_minutes"><option v-for="value in [5,10,30,60]" :key="value" :value="value">{{ value }} 分钟</option></select></label>
            <label>Bark 设备密钥或测试地址<input v-model="barkKey" type="password" :placeholder="bark.configured ? `已配置 ${bark.masked_key}，留空不修改` : '请输入密钥'" autocomplete="off"></label>
            <div class="form-actions"><button class="primary" :disabled="busy" type="submit">保存告警设置</button><button class="secondary" type="button" @click="testBark">测试手机推送</button><button v-if="bark.web_sound_enabled && !soundReady" class="secondary" type="button" @click="unlockWebSound()">允许当前浏览器播放</button></div>
          </form></Panel></div></template>
      </main>
      <footer>数据每 5 秒刷新 · 手机告警由服务端 Bark 推送</footer>
    </div>
  </div>
  <div v-if="preview" class="modal-backdrop" @click.self="preview = ''"><div class="preview-card m-surface"><div class="preview-bar"><button class="secondary" @click="preview = ''">关闭截图</button><span v-if="previewTime" class="preview-time">截图时间：{{ previewTime }}</span><span v-if="historyCharacter" class="preview-time">账号 ID：{{ historyCharacter }}</span></div><div class="preview-image-row"><button v-if="historyCharacter" class="secondary preview-arrow" :disabled="historyBusy || historyIndex === 0" aria-label="更新一张历史截图" @click="navigateHistory(-1).catch(fail)">‹</button><img :src="preview" alt="游戏窗口截图" referrerpolicy="no-referrer" @error="previewError = '图片加载失败或链接已过期，请关闭后重试'"><button v-if="historyCharacter" class="secondary preview-arrow" :disabled="historyBusy || (!historyHasMore && historyIndex >= history.length - 1)" aria-label="更早一张历史截图" @click="navigateHistory(1).catch(fail)">›</button></div><p v-if="historyCharacter" class="preview-counter">第 {{ historyIndex + 1 }} / {{ historyTotal }} 张 · 从新到旧 · 左右方向键可翻看 · 保留最近 30 天</p><p v-if="previewError" class="message error">{{ previewError }}</p></div></div>
</template>
