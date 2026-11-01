export declare const DEFAULT_BASE: string;
export declare class MemTetherClient {
  baseUrl: string;
  apiKey: string;
  timeoutMs: number;
  constructor(opts?: { baseUrl?: string; apiKey?: string; timeoutMs?: number });
  health(): Promise<any>;
  stats(): Promise<any>;
  remember(content: string, opts?: { type?: string; source?: string; tenant?: string }): Promise<{ uid: string } & Record<string, unknown>>;
  search(query: string, opts?: { limit?: number; tenant?: string; scope?: string }): Promise<{ results: Array<{ uid: string; content: string; source: string; score: number }> } & Record<string, unknown>>;
  correct(uid: string, content: string, opts?: { reason?: string; by?: string }): Promise<any>;
  retire(uid: string, opts?: { reason?: string; by?: string }): Promise<any>;
  list(opts?: { limit?: number; type?: string }): Promise<any>;
  timeline(uid: string): Promise<any>;
  courtVerify(): Promise<any>;
  court(uid: string): Promise<any>;
}
