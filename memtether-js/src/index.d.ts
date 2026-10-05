
export declare function remember(content: string, options?: { source?: string; type?: string }): { ok: boolean; uid: string | null; raw: string };
export declare function search(query: string, limit?: number): { line: string; uid: string | null; score: number | null }[];
export declare function stats(): Record<string, number>;
