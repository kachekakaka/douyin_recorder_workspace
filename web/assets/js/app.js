import { checkRoom, createRoom, getRooms, getStatus, setRoomEnabled } from './api.js';

const byId = id => document.getElementById(id);

function setTool(prefix, tool) {
  const state = byId(`${prefix}State`);
  const version = byId(`${prefix}Version`);
  state.textContent = tool.ready ? '就绪' : '不可用';
  state.className = tool.ready ? 'good' : 'bad';
  version.textContent = tool.version || tool.error || tool.configured;
}

function showError(cause) {
  const error = byId('errorText');
  error.textContent = cause instanceof Error ? cause.message : String(cause);
  error.hidden = false;
}

function clearError() {
  byId('errorText').hidden = true;
}

function formatCheck(check) {
  if (!check) return '尚未检查';
  const state = check.live_state || 'unknown';
  const count = check.stream_candidate_count || 0;
  const title = check.title ? ` · ${check.title}` : '';
  return `${state} · ${count} 个流候选${title}`;
}

function actionButton(label, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary';
  button.textContent = label;
  button.addEventListener('click', async () => {
    button.disabled = true;
    clearError();
    try {
      await action();
      await refreshRooms();
    } catch (cause) {
      showError(cause);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function renderRooms(items) {
  const list = byId('roomList');
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '还没有直播间。先添加一个抖音号或 live.douyin.com 地址。';
    list.append(empty);
    return;
  }
  for (const room of items) {
    const card = document.createElement('article');
    card.className = 'room-card';

    const heading = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = room.room_key;
    const url = document.createElement('small');
    url.textContent = room.room_url;
    heading.append(title, url);

    const meta = document.createElement('p');
    meta.textContent = `${room.enabled ? '已启用' : '已停用'} · ${room.protocol} · ${room.quality} · ${room.poll_interval_seconds}s`;

    const check = document.createElement('p');
    check.className = 'muted';
    check.textContent = formatCheck(room.latest_check);

    const actions = document.createElement('div');
    actions.className = 'actions';
    actions.append(
      actionButton('立即检查', () => checkRoom(room.room_key)),
      actionButton(room.enabled ? '停用' : '启用', () => setRoomEnabled(room.room_key, !room.enabled)),
    );

    card.append(heading, meta, check, actions);
    list.append(card);
  }
}

async function refreshRooms() {
  const data = await getRooms();
  renderRooms(data.items || []);
  byId('roomCount').textContent = String(data.total || 0);
}

async function refresh(force = false) {
  const button = byId('refreshButton');
  button.disabled = true;
  clearError();
  try {
    const [status] = await Promise.all([getStatus(force), refreshRooms()]);
    byId('appState').textContent = status.ready ? '已就绪' : '服务已启动';
    byId('appState').className = status.ready ? 'good' : '';
    byId('versionText').textContent = `v${status.version} · ${status.runtime_instance_id}`;
    byId('databaseState').textContent = `Schema v${status.schema_version}`;
    byId('databaseState').className = status.schema_version > 0 ? 'good' : 'bad';
    byId('databasePath').textContent = status.database_path;
    byId('roomCount').textContent = String(status.room_count || 0);
    setTool('ffmpeg', status.ffmpeg);
    setTool('ffprobe', status.ffprobe);
    const contract = status.protocol_contract;
    byId('protocolState').textContent = contract.live_verified ? '现场已验证' : '暂定 / 未验证';
    byId('protocolState').className = contract.live_verified ? 'good' : 'warn';
    byId('protocolHash').textContent = `${contract.status} · ${contract.sha256.slice(0, 16)}…`;
  } catch (cause) {
    showError(cause);
    byId('appState').textContent = '连接失败';
    byId('appState').className = 'bad';
  } finally {
    button.disabled = false;
  }
}

byId('roomForm').addEventListener('submit', async event => {
  event.preventDefault();
  const submit = byId('roomSubmit');
  submit.disabled = true;
  clearError();
  try {
    await createRoom({
      room_key: byId('roomKey').value,
      room_url: byId('roomUrl').value,
      quality: byId('roomQuality').value,
      protocol: byId('roomProtocol').value,
    });
    event.currentTarget.reset();
    byId('roomQuality').value = 'origin';
    byId('roomProtocol').value = 'flv';
    await refresh(true);
  } catch (cause) {
    showError(cause);
  } finally {
    submit.disabled = false;
  }
});

byId('refreshButton').addEventListener('click', () => refresh(true));
refresh();
