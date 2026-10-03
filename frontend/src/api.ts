export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    let msg = text || res.statusText
    try {
      const j = JSON.parse(text)
      if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* 保留原文 */ }
    throw new Error(msg)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}
