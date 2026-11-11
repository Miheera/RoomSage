const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL ?? '';
const TOKEN_KEY = 'roomsage-access-token';

export interface Account { user_id: string; email: string; access_token?: string; token_type?: string; }
export interface RoomType { type: string; name: string; }
export interface Coverage { photo_count: number; '2d_ready': boolean; '3d_ready': boolean; photos_needed: number; }
export interface DesignSummary { id: string; status: string; starred: boolean; style: string; palette: string; image_url: string | null; created_at: string | null; }
export interface Room { id: string; project_id: string; type: string; name: string; budget: number | null; status: string; photo_count: number; has_2d: boolean; can_3d: boolean; designs: DesignSummary[]; }
export interface Project { id: string; name: string; total_budget: number; rooms: Room[]; }
export interface BudgetRole {
  role: string;
  minimum_quantity: number;
  maximum_quantity: number;
  min_item_price: number;
  max_item_price: number;
}
export interface BudgetRange { min: number; max: number; currency: 'INR'; roles_used: BudgetRole[]; }
export interface CatalogStyle { style: string; name: string; items_per_role: Record<string, number>; minimum_budget: number; }
export interface DesignItem {
  id: string; catalog_item_id: string; title: string; category: string; role: string;
  kind: 'object' | 'surface'; qty: number; price: number; line_total: number;
  buy_url: string; image_url: string | null; x: number | null; y: number | null;
  visible: boolean; visibility_reason?: string | null; is_estimate: boolean; display_label: string | null;
}
export interface DesignResult {
  id: string; room_id: string; parent_id: string | null; style: string; palette: string;
  status: 'queued' | 'running' | 'done' | 'failed'; progress: number; job_id: string | null;
  image_url: string | null; image_stale: boolean; error: string | null;
  render_metadata?: { render_quality?: string; remote_fallback_reason?: string; [key: string]: unknown };
  budget: number | null; items: DesignItem[]; total_cost: number; estimates: unknown[]; seed: number;
}

export function storeToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export async function fetchAuthenticatedImage(path: string): Promise<string> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  // Design responses already return a complete /api/v1/... path.
  const response = await fetch(`${API_BASE}${path}`, { headers });
  if (!response.ok) throw new Error(`Image request failed (${response.status}).`);
  return URL.createObjectURL(await response.blob());
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error: any = new Error(body.detail || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

const json = (method: string, data?: unknown): RequestInit => ({ method, body: data === undefined ? undefined : JSON.stringify(data) });

export const api = {
  register: (email: string, password: string) => request<Account>('/auth/register', json('POST', { email, password })),
  login: (email: string, password: string) => request<Account>('/auth/login', json('POST', { email, password })),
  me: () => request<Account>('/auth/me'),
  getProjects: () => request<Project[]>('/projects'),
  getProject: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (name: string) => request<{ id: string }>('/projects', json('POST', { name })),
  getRoomTypes: () => request<RoomType[]>('/catalog/room-types'),
  getRoomStyles: (roomType: string) => request<CatalogStyle[]>(`/catalog/room-types/${encodeURIComponent(roomType)}/styles`),
  createRoom: (projectId: string, type: string, name: string) => request<Room>(`/projects/${projectId}/rooms`, json('POST', { type, name })),
  updateRoom: (projectId: string, roomId: string, data: { name?: string; status?: string }) => request<Room>(`/projects/${projectId}/rooms/${roomId}`, json('PATCH', data)),
  deleteRoom: (projectId: string, roomId: string) => request<void>(`/projects/${projectId}/rooms/${roomId}`, { method: 'DELETE' }),
  getRoom: (roomId: string) => request<Room>(`/rooms/${roomId}`),
  getCoverage: (roomId: string) => request<Coverage>(`/rooms/${roomId}/coverage`),
  getBudgetRange: (roomId: string, style?: string) => request<BudgetRange>(`/rooms/${roomId}/budget-range${style ? `?style=${encodeURIComponent(style)}` : ''}`),
  setBudget: (roomId: string, budget: number, style?: string) => request<Room>(`/rooms/${roomId}/budget`, json('POST', { budget, style })),
  uploadRoomPhotos: (roomId: string, files: File[], labels: string[] = []) => {
    const form = new FormData();
    files.forEach(file => form.append('files', file));
    labels.forEach(label => form.append('angle_labels', label));
    return request<{ room_id: string; uploaded: number; coverage: Coverage }>(`/rooms/${roomId}/photos`, { method: 'POST', body: form });
  },
  createDesign: (roomId: string, style: string, palette: string, seed?: number) => request<{ design_id: string; job_id: string; status: string; seed: number }>(`/rooms/${roomId}/designs`, json('POST', { style, palette, seed })),
  getDesign: (designId: string) => request<DesignResult>(`/designs/${designId}`),
  getAlternatives: (designId: string, itemId: string) => request<any[]>(`/designs/${designId}/items/${itemId}/alternatives`),
  swapDesignItem: (designId: string, itemId: string, catalogItemId: string) => request<DesignResult>(`/designs/${designId}/items/${itemId}/swap`, json('POST', { catalog_item_id: catalogItemId })),
  removeDesignItem: (designId: string, itemId: string) => request<DesignResult>(`/designs/${designId}/items/${itemId}`, { method: 'DELETE' }),
  renderDesign: (designId: string) => request<{ design_id: string; parent_id: string; job_id: string; status: string; seed: number }>(`/designs/${designId}/render`, json('POST')),
};
