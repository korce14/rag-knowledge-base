import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';

export function useApi() {
  const token = ref(localStorage.getItem('rag_token') || '');
  const user = ref<any>(null);
  const username = ref('korce');
  const password = ref('');

  const headers = computed(() => ({
    Authorization: `Bearer ${token.value}`,
    'Content-Type': 'application/json',
  }));

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

  async function boot() {
    try {
      user.value = await api('/api/auth/me');
    } catch (error: any) {
      ElMessage.error(error.message);
    }
  }

  function logout() {
    token.value = '';
    user.value = null;
    localStorage.removeItem('rag_token');
  }

  return { token, user, username, password, api, login, logout, boot };
}
