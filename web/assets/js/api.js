async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    const detail = payload?.detail || payload?.error;
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return payload.data;
}

export function getStatus(refresh = false) {
  return request(`/api/status${refresh ? '?refresh=true' : ''}`);
}

export function getRooms() {
  return request('/api/rooms');
}

export function createRoom(room) {
  return request('/api/rooms', { method: 'POST', body: JSON.stringify(room) });
}

export function checkRoom(roomKey) {
  return request(`/api/rooms/${encodeURIComponent(roomKey)}/actions/check`, { method: 'POST' });
}

export function setRoomEnabled(roomKey, enabled) {
  const action = enabled ? 'enable' : 'disable';
  return request(`/api/rooms/${encodeURIComponent(roomKey)}/actions/${action}`, { method: 'POST' });
}
