export const TOKEN_KEY = 'hoyo.accessToken'
export const USERNAME_KEY = 'hoyo.username'

export class Unauthorized extends Error {}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body != null && typeof options.body === 'string') headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...options, headers, cache: 'no-store' })
  const result = await response.json().catch(() => ({}))
  if (response.status === 401) throw new Unauthorized(result.detail || '登录已失效')
  if (!response.ok) throw new Error(result.detail || `请求失败 (${response.status})`)
  return result as T
}

export function json(method: string, value: unknown): RequestInit {
  return { method, body: JSON.stringify(value) }
}

export function time(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

export function online(value?: string | null): boolean {
  const at = Date.parse(value || '')
  return Number.isFinite(at) && Date.now() - at < 90_000
}
