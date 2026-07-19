export async function getStatus(refresh = false) {
  const response = await fetch(`/api/status${refresh ? '?refresh=true' : ''}`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    credentials: 'same-origin',
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `状态请求失败（HTTP ${response.status}）`);
  }
  return payload.data;
}
