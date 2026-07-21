import {
  cancelJob,
  checkRoom,
  createExport,
  createRoom,
  getJobs,
  getManagerStatus,
  getRecording,
  getRooms,
  getStatus,
  getWorker,
  reconcileManager,
  retryJob,
  setRoomEnabled,
  startRecording,
  stopRecording,
} from './api.js';

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

function formatRecording(recording) {
  const session = recording?.session;
  if (!session) return '尚无录制 Session';
  const progress = session.last_progress || {};
  const seconds = Number.isFinite(progress.out_time_us)
    ? Math.max(0, Math.floor(progress.out_time_us / 1_000_000))
    : null;
  const suffix = seconds === null ? '' : ` · ${seconds}s`;
  return `${recording.active ? '录制中' : session.status} · ${session.recording_protocol || '-'} · ${session.recording_quality || '-'}${suffix}`;
}


function formatWorker(worker) {
  if (!worker) return 'worker 状态不可用';
  const live = worker.last_live_state || 'unknown';
  const next = worker.next_check_at_ms
    ? ` · 下次 ${new Date(worker.next_check_at_ms).toLocaleTimeString()}`
    : '';
  const error = worker.last_error_code ? ` · ${worker.last_error_code}` : '';
  return `${worker.lifecycle} · ${live} · offline ${worker.consecutive_offline} · errors ${worker.consecutive_errors}${next}${error}`;
}

function actionButton(label, action, { disabled = false, className = 'secondary' } = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = label;
  button.disabled = disabled;
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

    const worker = document.createElement('p');
    worker.className = room.worker?.running ? 'worker-active' : 'muted';
    worker.textContent = formatWorker(room.worker);

    const recording = document.createElement('p');
    recording.className = room.recording?.active ? 'recording-active' : 'muted';
    recording.textContent = formatRecording(room.recording);

    const actions = document.createElement('div');
    actions.className = 'actions';
    actions.append(
      actionButton('立即检查', () => checkRoom(room.room_key)),
      room.recording?.active
        ? actionButton('停止录制', () => stopRecording(room.room_key), { className: 'danger' })
        : actionButton('开始录制', () => startRecording(room.room_key), {
            disabled: !room.enabled,
            className: 'record',
          }),
      room.recording?.session?.status === 'ended'
        ? actionButton('创建区间导出', () => createExport(room.recording.session.id), {
            className: 'export',
          })
        : document.createDocumentFragment(),
      actionButton(room.enabled ? '停用' : '启用', () => setRoomEnabled(room.room_key, !room.enabled)),
    );

    card.append(heading, meta, check, worker, recording, actions);
    list.append(card);
  }
}

function formatJob(job) {
  const outputs = job.outputs || [];
  const completed = outputs.filter(item => item.status === 'succeeded').length;
  return `${job.status} · ${completed}/${outputs.length} 输出 · ${job.attempts}/${job.max_attempts} 尝试`;
}

function renderJobs(items) {
  const list = byId('jobList');
  list.replaceChildren();
  byId('jobCount').textContent = String(items.length);
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = '尚无后处理任务。录制 Session 正常结束后可创建 recipient 区间导出。';
    list.append(empty);
    return;
  }
  for (const job of items) {
    const card = document.createElement('article');
    card.className = 'job-card';
    const title = document.createElement('strong');
    title.textContent = job.id;
    const meta = document.createElement('p');
    meta.textContent = formatJob(job);
    const session = document.createElement('small');
    session.textContent = `Session ${job.session_id}`;
    const outputs = document.createElement('ul');
    for (const output of job.outputs || []) {
      const row = document.createElement('li');
      row.textContent = `${output.interval_status} · ${output.status} · ${output.relative_path}`;
      outputs.append(row);
    }
    const actions = document.createElement('div');
    actions.className = 'actions';
    if (job.status === 'queued' || job.status === 'running') {
      actions.append(actionButton('取消任务', () => cancelJob(job.id), { className: 'danger' }));
    }
    if (job.status === 'failed' || job.status === 'canceled') {
      actions.append(actionButton('重试任务', () => retryJob(job.id)));
    }
    card.append(title, meta, session, outputs, actions);
    list.append(card);
  }
}

async function refreshJobs() {
  const data = await getJobs();
  renderJobs(data.items || []);
}

async function refreshRooms() {
  const data = await getRooms();
  const rooms = data.items || [];
  const items = await Promise.all(
    rooms.map(async room => {
      const [recording, worker] = await Promise.all([
        getRecording(room.room_key).catch(() => null),
        getWorker(room.room_key).catch(() => null),
      ]);
      return { ...room, recording, worker };
    }),
  );
  renderRooms(items);
  byId('roomCount').textContent = String(data.total || 0);
}

async function refresh(force = false) {
  const button = byId('refreshButton');
  button.disabled = true;
  clearError();
  try {
    const [status, manager] = await Promise.all([
      getStatus(force),
      getManagerStatus(),
      refreshRooms(),
      refreshJobs(),
    ]);
    byId('appState').textContent = status.ready ? '已就绪' : '服务已启动';
    byId('appState').className = status.ready ? 'good' : '';
    byId('versionText').textContent = `v${status.version} · ${status.runtime_instance_id}`;
    byId('databaseState').textContent = `Schema v${status.schema_version}`;
    byId('databaseState').className = status.schema_version > 0 ? 'good' : 'bad';
    byId('databasePath').textContent = status.database_path;
    byId('roomCount').textContent = String(status.room_count || 0);
    byId('managerState').textContent = manager.enabled
      ? manager.running ? '运行中' : '已启用 / 未运行'
      : '已禁用';
    byId('managerState').className = manager.running ? 'good' : manager.enabled ? 'warn' : '';
    byId('managerDetail').textContent = `${manager.worker_count} 个 worker · 并发检查 ${manager.max_parallel_checks}`;
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
byId('managerReconcile').addEventListener('click', async () => {
  const button = byId('managerReconcile');
  button.disabled = true;
  clearError();
  try {
    await reconcileManager();
    await refresh(true);
  } catch (cause) {
    showError(cause);
  } finally {
    button.disabled = false;
  }
});
refresh();
