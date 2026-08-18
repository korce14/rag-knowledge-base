import { ref } from 'vue';

export function useCodingAgent(api: (path: string, options?: RequestInit) => Promise<any>) {
  const task = ref('');
  const projectPath = ref('');
  const maxSteps = ref(8);
  const running = ref(false);
  const result = ref<any>(null);

  async function run() {
    running.value = true;
    result.value = null;
    try {
      result.value = await api('/api/coding-agent/run', {
        method: 'POST',
        body: JSON.stringify({
          task: task.value,
          project_path: projectPath.value,
          max_steps: maxSteps.value,
        }),
      });
    } finally {
      running.value = false;
    }
  }

  return { task, projectPath, maxSteps, running, result, run };
}
