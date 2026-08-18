<template>
  <div class="admin-shell">
    <header class="admin-header">
      <div>
        <h1>RAG 管理后台</h1>
        <p class="subtitle">Vue3 + Element Plus + TypeScript</p>
      </div>
      <div class="header-actions">
        <span v-if="user">{{ user.username }}（{{ user.role }}）</span>
        <el-button v-if="user" @click="logout">退出</el-button>
      </div>
    </header>

    <div v-if="!token" class="login-card">
      <el-input v-model="username" placeholder="用户名" />
      <el-input v-model="password" type="password" placeholder="密码" show-password />
      <el-button type="primary" @click="login">登录</el-button>
    </div>

    <el-tabs v-else v-model="activeTab" class="admin-tabs">
      <el-tab-pane label="分析" name="analytics">
        <el-select v-model="kbId" placeholder="选择知识库" @change="loadAll">
          <el-option v-for="kb in kbs" :key="kb.id" :label="kb.name" :value="kb.id" />
        </el-select>
        <el-button @click="loadAnalytics">刷新</el-button>
        <el-button type="success" @click="downloadReport">导出报告 CSV</el-button>
        <el-button type="primary" @click="generateToc">生成目录与概述</el-button>
        <el-button type="warning" @click="generateOverview">生成概述</el-button>
        <div v-if="report" class="cards">
          <el-card class="stat-card">
            <div class="stat-num">{{ report.total_questions }}</div>
            <div class="stat-label">问题总数</div>
          </el-card>
          <el-card class="stat-card">
            <div class="stat-num">{{ report.unresolved }}</div>
            <div class="stat-label">未解决</div>
          </el-card>
        </div>
        <el-table v-if="report" :data="report.top" style="margin-top: 16px">
          <el-table-column prop="question" label="问题" />
          <el-table-column prop="count" label="次数" width="100" />
          <el-table-column prop="unresolved" label="未解决" width="100" />
        </el-table>
        <el-table v-if="cards.length" :data="cards" style="margin-top: 16px">
          <el-table-column prop="question" label="问题" />
          <el-table-column prop="count" label="次数" width="80" />
          <el-table-column prop="unresolved" label="未解决" width="80" />
          <el-table-column prop="summary" label="摘要" />
          <el-table-column label="导出" width="100">
            <template #default="{ row }">
              <el-button size="small" @click="exportCard(row)">导出卡片</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="toc.overview" class="toc-box">
          <h3>知识库概述</h3>
          <p>{{ toc.overview }}</p>
          <h3>目录</h3>
          <ul>
            <li v-for="item in toc.toc" :key="item.title">{{ item.title }}</li>
          </ul>
        </div>
      </el-tab-pane>

      <el-tab-pane label="批量导入" name="batch">
        <el-alert type="info" :closable="false" title="支持 CSV / Excel，每行作为一个文档导入" />
        <el-input v-model="batchTags" placeholder="标签，逗号分隔" style="margin: 12px 0" />
        <input ref="batchInput" type="file" accept=".csv,.xlsx,.xlsm" hidden @change="batchImport" />
        <el-button type="primary" @click="pickBatch">选择批量文件</el-button>
        <pre v-if="batchResult" class="result-box">{{ batchResult }}</pre>
      </el-tab-pane>

      <el-tab-pane label="文件夹索引" name="folder">
        <el-input v-model="folderPath" placeholder="服务器上的文件夹绝对路径" style="margin-bottom: 12px" />
        <el-input v-model="folderTags" placeholder="标签，逗号分隔" style="margin-bottom: 12px" />
        <el-button type="primary" @click="indexFolder">开始索引</el-button>
        <pre v-if="folderResult" class="result-box">{{ folderResult }}</pre>
      </el-tab-pane>

      <el-tab-pane label="数据源" name="sources">
        <div class="source-form">
          <el-select v-model="source.kind" placeholder="类型">
            <el-option label="RSS" value="rss" />
            <el-option label="数据库" value="db" />
            <el-option label="API" value="api" />
          </el-select>
          <el-input v-model="source.name" placeholder="名称" />
          <el-input v-model="source.configText" type="textarea" :rows="3" placeholder='配置 JSON，例如 {"url": "https://..."}' />
          <el-input-number v-model="source.intervalMinutes" :min="1" />
          <el-button type="primary" @click="createSource">添加</el-button>
        </div>
        <el-table :data="sources" style="margin-top: 16px">
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="kind" label="类型" width="100" />
          <el-table-column prop="last_synced_at" label="上次同步" />
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button size="small" @click="syncSource(row)">同步</el-button>
              <el-button size="small" type="danger" @click="removeSource(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="API Key" name="keys">
        <el-input v-model="keyName" placeholder="名称" style="width: 240px" />
        <el-button type="primary" @click="createKey">创建</el-button>
        <div v-if="newKey" class="key-result">新 Key：{{ newKey }}（只显示一次）</div>
        <el-table :data="apiKeys" style="margin-top: 16px">
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="last_used_at" label="最近使用" />
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button size="small" type="danger" @click="revokeKey(row)">回收</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="密码" name="password">
        <el-input v-model="oldPassword" type="password" placeholder="原密码" show-password />
        <el-input v-model="newPassword" type="password" placeholder="新密码（至少 8 位）" show-password style="margin-top: 8px" />
        <el-button type="primary" @click="changePassword">修改密码</el-button>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';

const token = ref(localStorage.getItem('rag_token') || '');
const user = ref<any>(null);
const username = ref('korce');
const password = ref('');
const activeTab = ref('analytics');
const kbs = ref<any[]>([]);
const kbId = ref('');
const report = ref<any>(null);
const toc = ref<any>({ toc: [], overview: '' });
const cards = ref<any[]>([]);

const batchTags = ref('');
const batchInput = ref<HTMLInputElement | null>(null);
const batchResult = ref('');
const folderPath = ref('');
const folderTags = ref('');
const folderResult = ref('');

const source = ref<any>({ kind: 'rss', name: '', configText: '{"url": ""}', intervalMinutes: 60 });
const sources = ref<any[]>([]);
const keyName = ref('');
const newKey = ref('');
const apiKeys = ref<any[]>([]);
const oldPassword = ref('');
const newPassword = ref('');

const headers = computed(() => ({ Authorization: `Bearer ${token.value}`, 'Content-Type': 'application/json' }));

async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers.value, ...(options.headers || {}) },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

async function login() {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: username.value, password: password.value }),
  });
  if (!response.ok) {
    ElMessage.error('登录失败');
    return;
  }
  const data = await response.json();
  token.value = data.token;
  localStorage.setItem('rag_token', data.token);
  await boot();
}

function logout() {
  token.value = '';
  user.value = null;
  localStorage.removeItem('rag_token');
}

async function boot() {
  try {
    user.value = await api('/api/auth/me');
    kbs.value = await api('/api/knowledge_bases');
    if (kbs.value.length) {
      kbId.value = kbs.value[0].id;
      await loadAll();
    }
  } catch (error: any) {
    ElMessage.error(error.message);
  }
}

async function loadAll() {
  if (!kbId.value) return;
  await Promise.all([loadAnalytics(), loadCards(), loadSources(), loadKeys(), loadToc()]);
}

async function loadAnalytics() {
  if (!kbId.value) return;
  report.value = await api(`/api/analytics/report?kb_id=${kbId.value}`);
}

async function loadCards() {
  if (!kbId.value) return;
  cards.value = await api(`/api/analytics/cards?kb_id=${kbId.value}`);
}

async function loadToc() {
  if (!kbId.value) return;
  toc.value = await api(`/api/knowledge_bases/${kbId.value}/toc`);
}

function downloadReport() {
  if (!kbId.value) return;
  window.open(`/api/analytics/report.csv?kb_id=${kbId.value}`, '_blank');
}

async function generateToc() {
  if (!kbId.value) return;
  toc.value = await api(`/api/knowledge_bases/${kbId.value}/toc`, { method: 'POST', body: '{}' });
  ElMessage.success('目录与概述已生成');
}

async function generateOverview() {
  if (!kbId.value) return;
  const result = await api(`/api/knowledge_bases/${kbId.value}/overview`, { method: 'POST', body: '{}' });
  toc.value = { ...toc.value, overview: result.overview };
  ElMessage.success('概述已生成');
}

function exportCard(row: any) {
  if (!kbId.value) return;
  window.open(`/api/analytics/cards/${row.id}/export?kb_id=${kbId.value}`, '_blank');
}

function pickBatch() {
  batchInput.value?.click();
}

async function batchImport(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file || !kbId.value) return;
  const form = new FormData();
  form.append('file', file);
  form.append('tags', batchTags.value);
  form.append('mode', 'document');
  const response = await fetch(`/api/knowledge_bases/${kbId.value}/batch-import`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token.value}` },
    body: form,
  });
  if (!response.ok) {
    ElMessage.error('提交失败');
    return;
  }
  const task = await response.json();
  ElMessage.success('已提交批量导入任务');
  for (let i = 0; i < 120; i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const status = await api(`/api/tasks/${task.task_id}`);
    if (status.status === 'done') {
      batchResult.value = JSON.stringify(status.result, null, 2);
      break;
    }
    if (status.status === 'error') {
      batchResult.value = status.error;
      break;
    }
  }
}

async function indexFolder() {
  if (!kbId.value || !folderPath.value) return;
  const task = await api(`/api/knowledge_bases/${kbId.value}/folder-index`, {
    method: 'POST',
    body: JSON.stringify({ folder_path: folderPath.value }),
  });
  for (let i = 0; i < 240; i++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const status = await api(`/api/tasks/${task.task_id}`);
    if (status.status === 'done') {
      folderResult.value = JSON.stringify(status.result, null, 2);
      break;
    }
    if (status.status === 'error') {
      folderResult.value = status.error;
      break;
    }
  }
}

async function loadSources() {
  if (!kbId.value) return;
  sources.value = await api(`/api/knowledge_bases/${kbId.value}/sources`);
}

async function createSource() {
  if (!kbId.value) return;
  await api(`/api/knowledge_bases/${kbId.value}/sources`, {
    method: 'POST',
    body: JSON.stringify({
      kind: source.value.kind,
      name: source.value.name,
      config: JSON.parse(source.value.configText || '{}'),
      interval_minutes: source.value.intervalMinutes,
    }),
  });
  ElMessage.success('数据源已添加');
  source.value = { kind: 'rss', name: '', configText: '{"url": ""}', intervalMinutes: 60 };
  await loadSources();
}

async function syncSource(row: any) {
  await api(`/api/sources/${row.id}/sync`, { method: 'POST', body: '{}' });
  ElMessage.success('同步完成');
  await loadSources();
}

async function removeSource(row: any) {
  await api(`/api/sources/${row.id}`, { method: 'DELETE' });
  await loadSources();
}

async function loadKeys() {
  apiKeys.value = await api('/api/api-keys');
}

async function createKey() {
  const result = await api('/api/api-keys', { method: 'POST', body: JSON.stringify({ name: keyName.value }) });
  newKey.value = result.key;
  keyName.value = '';
  await loadKeys();
}

async function revokeKey(row: any) {
  await api(`/api/api-keys/${row.id}`, { method: 'DELETE' });
  await loadKeys();
}

async function changePassword() {
  await api('/api/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword.value, new_password: newPassword.value }),
  });
  ElMessage.success('密码已修改');
  oldPassword.value = '';
  newPassword.value = '';
}

onMounted(() => {
  if (token.value) boot();
});
</script>

<style>
body { margin: 0; background: #f5f7fa; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; }
.admin-shell { max-width: 1100px; margin: 0 auto; padding: 24px; }
.admin-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.admin-header h1 { margin: 0; }
.subtitle { color: #888; margin: 4px 0 0; }
.login-card { max-width: 360px; margin: 80px auto; display: grid; gap: 12px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-top: 16px; }
.stat-num { font-size: 32px; font-weight: 700; }
.stat-label { color: #888; }
.result-box, .key-result, .toc-box { white-space: pre-wrap; background: #fff; border: 1px solid #e4e7ed; border-radius: 8px; padding: 12px; margin-top: 12px; }
.source-form { display: grid; grid-template-columns: 120px 1fr 1fr 140px auto; gap: 8px; align-items: start; }
</style>
