const state = {
  token: localStorage.getItem("rag_token") || "",
  user: JSON.parse(localStorage.getItem("rag_user") || "null"),
  kbs: [],
  documents: [],
  currentKbId: null,
  sessionId: localStorage.getItem("rag_session_id") || "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  return fetch(path, { ...options, headers });
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 2800);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderMarkdown(text) {
  const escaped = escapeHtml(text);
  const withCode = escaped.replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>");
  const withInline = withCode.replace(/`([^`]+)`/g, "<code>$1</code>");
  const withBold = withInline.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const withHeadings = withBold.replace(/^### (.+)$/gm, "<h4>$1</h4>").replace(/^## (.+)$/gm, "<h3>$1</h3>").replace(/^# (.+)$/gm, "<h2>$1</h2>");
  const withLists = withHeadings.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
  return withLists.replace(/\n/g, "<br>");
}

function capabilityBadge(label, active) {
  return `<span class="capability ${active ? "active" : ""}">${label}</span>`;
}

function renderAuthState() {
  const loggedIn = Boolean(state.token && state.user);
  const icon = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.6-2-3.4-2.4 1A7 7 0 0 0 15 6l-.4-2.5h-4L10.2 6a7 7 0 0 0-1.6 1l-2.4-1-2 3.4L6.2 11a7 7 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 1.6 1l.4 2.5h4l.4-2.5a7 7 0 0 0 1.6-1l2.4 1 2-3.4-2-1.6a7 7 0 0 0 .1-1Z"/></svg>`;
  $("#settingsButton").innerHTML = `${icon}${loggedIn ? `${escapeHtml(state.user.username)} / 退出` : "登录 / 用户"}`;
  $("#logoutButton").classList.toggle("hidden", !loggedIn);
}

async function loadSettings() {
  const response = await api("/api/settings");
  if (response.status === 401) {
    logout();
    openAuth();
    return;
  }
  const data = await response.json();
  $("#capabilities").innerHTML = [
    capabilityBadge(data.dense_enabled ? "向量检索" : "关键词检索", data.dense_enabled),
    capabilityBadge(data.generation_enabled ? "生成模型" : "保守摘要", data.generation_enabled),
    capabilityBadge(data.rerank_enabled ? "重排序" : "基础排序", data.rerank_enabled),
  ].join("");
}

async function loadKbs() {
  const response = await api("/api/knowledge_bases");
  if (response.status === 401) return;
  if (!response.ok) return;
  state.kbs = await response.json();
  renderKbs();
  if (!state.currentKbId && state.kbs.length) selectKb(state.kbs[0].id);
}

function renderKbs() {
  const list = $("#kbList");
  if (!state.kbs.length) {
    list.innerHTML = `<div class="document-item">还没有可访问的知识库</div>`;
    return;
  }
  list.innerHTML = state.kbs
    .map(
      (kb) => `
        <div class="kb-item-row ${kb.id === state.currentKbId ? "active" : ""}">
          <button class="kb-item" data-id="${kb.id}">${escapeHtml(kb.name)}</button>
          <button class="icon-button small" data-action="permissions" data-id="${kb.id}" aria-label="权限">⚙</button>
          <button class="icon-button small danger" data-action="delete" data-id="${kb.id}" aria-label="删除">×</button>
        </div>`,
    )
    .join("");
  list.querySelectorAll(".kb-item").forEach((button) => {
    button.addEventListener("click", () => selectKb(button.dataset.id));
  });
  list.querySelectorAll("[data-action=\"permissions\"]").forEach((button) => {
    button.addEventListener("click", () => openKbPermissions(button.dataset.id));
  });
  list.querySelectorAll("[data-action=\"delete\"]").forEach((button) => {
    button.addEventListener("click", () => deleteKb(button.dataset.id));
  });
}

async function openDocumentViewer(documentId) {
  state.viewingDocumentId = documentId;
  const response = await api(`/api/documents/${documentId}/content`);
  if (!response.ok) {
    showToast("无法查看文档");
    return;
  }
  const data = await response.json();
  $("#documentViewerTitle").textContent = data.name;
  $("#documentViewerContent").textContent = data.text;
  $("#documentAnswer").textContent = "";
  $("#documentQuestion").value = "";
  $("#documentViewerDialog").classList.remove("hidden");
}

function closeDocumentViewer() {
  $("#documentViewerDialog").classList.add("hidden");
}

async function askDocument() {
  if (!state.viewingDocumentId) return;
  const question = $("#documentQuestion").value.trim();
  if (!question) return;
  $("#documentAnswer").textContent = "正在生成...";
  const response = await api("/api/chat", {
    method: "POST",
    body: JSON.stringify({
      kb_id: state.currentKbId,
      question,
      document_id: state.viewingDocumentId,
      session_id: state.sessionId,
      top_k: 5,
    }),
  });
  const data = await response.json();
  $("#documentAnswer").textContent = data.answer || "请求失败。";
}

async function selectKb(kbId) {
  state.currentKbId = kbId;
  const kb = state.kbs.find((item) => item.id === kbId);
  $("#currentKbName").textContent = kb?.name || "未命名知识库";
  $("#currentKbDesc").textContent = kb?.description || "从这个知识库中检索并回答问题。";
  renderKbs();
  $("#uploadPanel").classList.remove("hidden");
  $("#emptyState").classList.remove("hidden");
  $("#chat").querySelectorAll(".message-row").forEach((node) => node.remove());
  await loadSessionMessages();
  await loadDocuments();
}

async function loadDocuments() {
  if (!state.currentKbId) return;
  const response = await api(`/api/knowledge_bases/${state.currentKbId}/documents`);
  if (!response.ok) return;
  state.documents = await response.json();
  const list = $("#documentList");
  if (!state.documents.length) {
    list.innerHTML = `<div class="document-item">还没有导入文档</div>`;
    return;
  }
  list.innerHTML = state.documents
    .map(
      (doc) => `
        <div class="document-item">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-4Z"/><path d="M14 3v4h4"/></svg>
          <button class="document-name-button" data-view="${doc.id}">${escapeHtml(doc.name)}</button>
          <button class="icon-button" data-id="${doc.id}" aria-label="删除">×</button>
        </div>`,
    )
    .join("");
  list.querySelectorAll("button[data-view]").forEach((button) => {
    button.addEventListener("click", () => openDocumentViewer(button.dataset.view));
  });
  list.querySelectorAll("button[data-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/documents/${button.dataset.id}`, { method: "DELETE" });
      showToast("文档已删除");
      await loadDocuments();
    });
  });
}

async function loadSessionMessages() {
  if (!state.sessionId || !state.currentKbId) return;
  const response = await api(`/api/sessions/${state.sessionId}/messages`);
  if (!response.ok) return;
  const messages = await response.json();
  for (const message of messages) {
    addHistoryMessage(message.role, message.content, message.id, message.sources || []);
  }
}

function addHistoryMessage(role, text, messageId, sources = []) {
  const row = addMessage(role, text, messageId);
  if (sources.length) addSources(row.querySelector(".message"), sources);
}

async function clearSessionMessages() {
  if (!state.sessionId) return;
  if (!window.confirm("确定清空当前对话记录？")) return;
  const response = await api(`/api/sessions/${state.sessionId}/messages`, { method: "DELETE" });
  if (response.ok) {
    $("#chat").querySelectorAll(".message-row").forEach((node) => node.remove());
    $("#emptyState").classList.remove("hidden");
    showToast("对话记录已清空");
  }
}

async function deleteKb(kbId) {
  const kb = state.kbs.find((item) => item.id === kbId);
  if (!window.confirm(`确定删除知识库：${kb?.name || kbId}？`)) return;
  const response = await api(`/api/knowledge_bases/${kbId}`, { method: "DELETE" });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail || "删除失败";
    showToast(detail);
    return;
  }
  if (state.currentKbId === kbId) state.currentKbId = null;
  await loadKbs();
  showToast("知识库已删除");
}

async function openKbPermissions(kbId) {
  state.kbPermissionsKbId = kbId;
  await loadKbPermissions();
  $("#kbPermissionsDialog").classList.remove("hidden");
}

async function loadKbPermissions() {
  if (!state.kbPermissionsKbId) return;
  const [usersResponse, permsResponse] = await Promise.all([
    api("/api/users"),
    api(`/api/knowledge_bases/${state.kbPermissionsKbId}/permissions`),
  ]);
  if (!usersResponse.ok || !permsResponse.ok) return;
  const users = await usersResponse.json();
  const permissions = await permsResponse.json();
  const userMap = Object.fromEntries(users.map((user) => [user.id, user.username]));
  const userSelect = $("#kbPermissionUser");
  userSelect.innerHTML = users.map((user) => `<option value="${user.id}">${escapeHtml(user.username)}</option>`).join("");
  const list = $("#kbPermissionList");
  if (!permissions.length) {
    list.innerHTML = `<div class="document-item">还没有授权</div>`;
    return;
  }
  list.innerHTML = permissions.map((item) => `
    <div class="document-item">
      <span>${escapeHtml(userMap[item.user_id] || item.user_id)} · ${escapeHtml(item.role)}</span>
      <button class="icon-button small danger" data-revoke-user="${item.user_id}">×</button>
    </div>`).join("");
  list.querySelectorAll("[data-revoke-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/knowledge_bases/${state.kbPermissionsKbId}/permissions/${button.dataset.revokeUser}`, { method: "DELETE" });
      await loadKbPermissions();
    });
  });
}

async function addKbPermission() {
  if (!state.kbPermissionsKbId) return;
  const user_id = $("#kbPermissionUser").value;
  const role = $("#kbPermissionRole").value;
  const response = await api(`/api/knowledge_bases/${state.kbPermissionsKbId}/permissions`, {
    method: "POST",
    body: JSON.stringify({ user_id, role }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail || "授权失败";
    showToast(detail);
    return;
  }
  await loadKbPermissions();
  showToast("授权已添加");
}

function closeKbPermissions() {
  $("#kbPermissionsDialog").classList.add("hidden");
}

async function openUsers() {
  $("#usersDialog").classList.remove("hidden");
  await loadUsers();
}

function closeUsers() {
  $("#usersDialog").classList.add("hidden");
}

async function loadUsers() {
  const response = await api("/api/users");
  if (!response.ok) return;
  const users = await response.json();
  const list = $("#userList");
  if (!users.length) {
    list.innerHTML = `<div class="document-item">没有用户</div>`;
    return;
  }
  list.innerHTML = users.map((user) => `
    <div class="document-item">
      <span>${escapeHtml(user.username)} · ${escapeHtml(user.role)}${user.is_active ? "" : " · 已禁用"}</span>
      <button class="icon-button small danger" data-delete-user="${user.id}">×</button>
    </div>`).join("");
  list.querySelectorAll("[data-delete-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/users/${button.dataset.deleteUser}`, { method: "DELETE" });
      await loadUsers();
    });
  });
}

async function createUser() {
  const username = $("#newUsername").value.trim();
  const password = $("#newPassword").value;
  const role = $("#newRole").value;
  const response = await api("/api/users", {
    method: "POST",
    body: JSON.stringify({ username, password, role, is_active: true }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail || "创建失败";
    showToast(detail);
    return;
  }
  $("#newUsername").value = "";
  $("#newPassword").value = "";
  await loadUsers();
  showToast("用户已创建");
}

async function createKb() {
  const name = window.prompt("知识库名称");
  if (!name?.trim()) return;
  const response = await api("/api/knowledge_bases", {
    method: "POST",
    body: JSON.stringify({ name: name.trim(), description: "" }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail || "创建失败";
    showToast(detail);
    return;
  }
  await loadKbs();
  const kb = state.kbs.find((item) => item.name === name.trim());
  if (kb) selectKb(kb.id);
}

async function uploadDocument() {
  if (!state.currentKbId) {
    showToast("请先选择或创建一个知识库");
    return;
  }
  const files = $("#fileInput").files;
  if (!files.length) return;
  const tags = $("#tagInput").value.trim();
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    form.append("tags", tags);
    const response = await fetch(`/api/knowledge_bases/${state.currentKbId}/documents`, {
      method: "POST",
      headers: state.token ? { Authorization: `Bearer ${state.token}` } : {},
      body: form,
    });
    if (response.status === 409) {
      showToast(`已存在相同文档：${file.name}`);
    } else if (!response.ok) {
      const detail = (await response.json().catch(() => ({}))).detail || "导入失败";
      showToast(`${file.name}：${detail}`);
    }
  }
  $("#fileInput").value = "";
  $("#tagInput").value = "";
  await loadDocuments();
  showToast("文档导入完成");
}

function addMessage(role, text, messageId = null) {
  $("#emptyState").classList.add("hidden");
  const row = document.createElement("div");
  row.className = "message-row";
  const renderedText = role === "assistant" ? renderMarkdown(text || "") : escapeHtml(text || "");
  row.innerHTML = `<div class="message ${role}"><div class="${role}-content">${renderedText}</div><div class="message-actions"><button class="icon-button small message-copy" aria-label="复制">⧉</button>${messageId ? `<button class="icon-button small danger message-delete" data-message-id="${messageId}" aria-label="删除">×</button>` : ""}</div></div>`;
  $("#chat").scrollTop = $("#chat").scrollHeight;
  row.querySelector(".message-copy").addEventListener("click", async (event) => {
    event.stopPropagation();
    await navigator.clipboard.writeText(text || "");
    showToast("已复制");
  });
  const deleteButton = row.querySelector(".message-delete");
  if (deleteButton) {
    deleteButton.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!window.confirm("删除这条消息？")) return;
      const response = await api(`/api/messages/${messageId}`, { method: "DELETE" });
      if (response.ok) row.remove();
    });
  }
  return row;
}
function addSources(row, sources) {
  if (!sources.length) return;
  const node = document.createElement("div");
  node.className = "sources";
  node.innerHTML = sources
    .map(
      (source) =>
        `<div class="source-item"><strong>[${source.context_index || source.chunk_index + 1}] ${escapeHtml(source.document_name)}</strong><br>${escapeHtml(source.text_preview)}</div>`,
    )
    .join("");
  row.appendChild(node);
}

async function sendMessage() {
  const input = $("#questionInput");
  const question = input.value.trim();
  if (!question || !state.currentKbId) {
    showToast("请先选择知识库并输入问题");
    return;
  }
  input.value = "";
  resizeTextarea();
  addMessage("user", question);
  const assistantRow = document.createElement("div");
  assistantRow.className = "message-row";
  assistantRow.innerHTML = `<div class="message assistant"><div class="assistant-progress hidden"></div><div class="assistant-content"></div></div>`;
  $("#chat").appendChild(assistantRow);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  const content = assistantRow.querySelector(".assistant-content");
  const progress = assistantRow.querySelector(".assistant-progress");

  const response = await api("/api/chat/stream", {
    method: "POST",
    body: JSON.stringify({
      kb_id: state.currentKbId,
      question,
      session_id: state.sessionId,
      top_k: 5,
    }),
  });

  if (response.status === 401) {
    logout();
    openAuth();
    return;
  }
  if (!response.ok || !response.body) {
    content.textContent = "请求失败，请稍后重试。";
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answerText = "";

  const processLine = (line) => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) return;
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start < 0 || end < 0) return;
    const event = JSON.parse(trimmed.slice(start, end + 1));
    if (event.type === "progress") {
      progress.textContent = event.message;
      progress.classList.remove("hidden");
    }
    if (event.type === "token") {
      progress.classList.add("hidden");
      answerText += event.content;
      content.innerHTML = renderMarkdown(answerText);
      $("#chat").scrollTop = $("#chat").scrollHeight;
    }
    if (event.type === "sources") {
      addSources(assistantRow.querySelector(".message"), event.sources);
    }
    if (event.type === "error") {
      content.textContent = event.reason;
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) processLine(line);
  }
  if (buffer.trim()) processLine(buffer);
  if (answerText) content.innerHTML = renderMarkdown(answerText);
  progress.classList.add("hidden");
  $("#chat").querySelectorAll(".message-row").forEach((node) => node.remove());
  await loadSessionMessages();
}

function resizeTextarea() {
  const input = $("#questionInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function openAuth() {
  $("#loginUsername").value = state.user?.username || "korce";
  $("#loginPassword").value = "";
  renderAuthState();
  $("#settingsDialog").classList.remove("hidden");
}

function closeAuth() {
  $("#settingsDialog").classList.add("hidden");
}

function logout() {
  state.token = "";
  state.user = null;
  state.kbs = [];
  state.documents = [];
  state.currentKbId = null;
  localStorage.removeItem("rag_token");
  localStorage.removeItem("rag_user");
  renderAuthState();
  $("#kbList").innerHTML = "";
  $("#documentList").innerHTML = "";
  $("#currentKbName").textContent = "选择一个知识库";
  $("#currentKbDesc").textContent = "从左侧选择或创建一个知识库开始。";
}

async function login(event) {
  event.preventDefault();
  const username = $("#loginUsername").value.trim();
  const password = $("#loginPassword").value;
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail || "登录失败";
    showToast(detail);
    return;
  }
  const data = await response.json();
  state.token = data.token;
  state.user = data.user;
  localStorage.setItem("rag_token", state.token);
  localStorage.setItem("rag_user", JSON.stringify(state.user));
  closeAuth();
  renderAuthState();
  await loadSettings();
  await loadKbs();
  showToast(`已登录：${state.user.username}`);
}

function setupEvents() {
  $("#newKbButton").addEventListener("click", createKb);
  $("#settingsButton").addEventListener("click", openAuth);
  $("#usersButton").addEventListener("click", openUsers);
  $("#closeSettings").addEventListener("click", closeAuth);
  $("#logoutButton").addEventListener("click", () => {
    logout();
    closeAuth();
  });
  $("#fileInput").addEventListener("change", uploadDocument);
  $("#sendButton").addEventListener("click", sendMessage);
  $("#clearHistoryButton").addEventListener("click", clearSessionMessages);
  $("#questionInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  $("#questionInput").addEventListener("input", resizeTextarea);
  $("#settingsForm").addEventListener("submit", login);
  $("#closeKbPermissions").addEventListener("click", closeKbPermissions);
  $("#addKbPermissionButton").addEventListener("click", addKbPermission);
  $("#closeUsers").addEventListener("click", closeUsers);
  $("#createUserButton").addEventListener("click", createUser);
  $("#closeDocumentViewer").addEventListener("click", closeDocumentViewer);
  $("#askDocumentButton").addEventListener("click", askDocument);
}

async function init() {
  setupEvents();
  if (!state.sessionId) {
    state.sessionId = crypto.randomUUID();
    localStorage.setItem("rag_session_id", state.sessionId);
  }
  renderAuthState();
  if (state.token) {
    await loadSettings();
    await loadKbs();
  } else {
    openAuth();
  }
  resizeTextarea();
}

init();









