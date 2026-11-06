const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL ?? '';

export interface Project { id: string; name: string; room_type?: string | null; photo_keys: string[]; }
export interface SceneResponse { room_type: string; primary_wall_color: string; existing_furniture: string[]; lighting_condition: string; structural_features: string[]; }
export interface BudgetRequest { theme: string; budget_cap_inr: number; }
export interface ProductItem { product_id: string; title: string; category: string; price_inr: number; buy_url: string; image_url?: string | null; }
export interface ShoppingListResponse { project_id: string; theme: string; budget_cap_inr: number; total_cost_inr: number; selected_items: ProductItem[]; paint_estimate_inr: number; flooring_estimate_inr: number; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api/v1${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error: any = new Error(body.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function poll<T>(path: string, attempts = 60, interval = 3000): Promise<T> {
  let lastError: unknown;
  for (let index = 0; index < attempts; index += 1) {
    try { return await request<T>(path); }
    catch (error: any) {
      if (![404, 409, 425].includes(error?.status)) throw error;
      lastError = error;
    }
    await delay(interval);
  }
  throw lastError instanceof Error ? lastError : new Error('Timed out waiting for the result.');
}

export const api = {
  createProject: (name: string, roomType?: string) => request<Project>('/projects', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, room_type: roomType }),
  }),
  uploadPhotos: async (projectId: string, files: File[], onProgress?: (percent: number) => void) => {
    const form = new FormData(); files.forEach(file => form.append('files', file));
    onProgress?.(0);
    const result = await request<{ project_id: string; photo_keys: string[] }>(`/projects/${projectId}/photos`, { method: 'POST', body: form });
    onProgress?.(100); return result;
  },
  getScene: (projectId: string) => poll<SceneResponse>(`/projects/${projectId}/scene`),
  generateBudget: (projectId: string, theme: string, budgetCapInr: number) => request<{ task_id: string; status: string }>(`/projects/${projectId}/budget`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ theme, budget_cap_inr: budgetCapInr }),
  }),
  getBudget: (projectId: string) => poll<ShoppingListResponse>(`/projects/${projectId}/budget`),
  refineChat: (projectId: string, message: string, currentPreviewPrompt = '', history: {role:string;content:string}[] = []) => request<{controlnet_prompt:string;negative_prompt:string;style_parameters:Record<string,unknown>}>(`/projects/${projectId}/chat`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message,current_preview_prompt:currentPreviewPrompt,chat_history:history}),
  }),
};
