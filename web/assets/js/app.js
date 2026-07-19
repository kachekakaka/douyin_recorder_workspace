import { getStatus } from './api.js';

const byId = id => document.getElementById(id);

function setTool(prefix, tool) {
  const state = byId(`${prefix}State`);
  const version = byId(`${prefix}Version`);
  state.textContent = tool.ready ? '就绪' : '不可用';
  state.className = tool.ready ? 'good' : 'bad';
  version.textContent = tool.version || tool.error || tool.configured;
}

async function refresh(force = false) {
  const button = byId('refreshButton');
  const error = byId('errorText');
  button.disabled = true;
  error.hidden = true;
  try {
    const status = await getStatus(force);
    byId('appState').textContent = status.ready ? '已就绪' : '服务已启动';
    byId('appState').className = status.ready ? 'good' : '';
    byId('versionText').textContent = `v${status.version} · ${status.runtime_instance_id}`;
    byId('databaseState').textContent = `Schema v${status.schema_version}`;
    byId('databaseState').className = status.schema_version > 0 ? 'good' : 'bad';
    byId('databasePath').textContent = status.database_path;
    setTool('ffmpeg', status.ffmpeg);
    setTool('ffprobe', status.ffprobe);
    const contract = status.protocol_contract;
    byId('protocolState').textContent = contract.live_verified ? '现场已验证' : '暂定 / 未验证';
    byId('protocolState').className = contract.live_verified ? 'good' : 'warn';
    byId('protocolHash').textContent = `${contract.status} · ${contract.sha256.slice(0, 16)}…`;
  } catch (cause) {
    error.textContent = cause instanceof Error ? cause.message : String(cause);
    error.hidden = false;
    byId('appState').textContent = '连接失败';
    byId('appState').className = 'bad';
  } finally {
    button.disabled = false;
  }
}

byId('refreshButton').addEventListener('click', () => refresh(true));
refresh();
