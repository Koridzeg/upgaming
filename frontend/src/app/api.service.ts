import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

export interface Citation {
  id: string;
  source_id: string;
  title: string;
  section: string;
  text: string;
  document_type: string | null;
  status: string | null;
  date: string | null;
  pages: number[] | null;
  char_start: number | null;
  char_end: number | null;
  suspicious: boolean; // the passage contains text that tries to instruct an AI
}

export interface Contradiction {
  topic: string;
  positions: { claim: string; evidence: string[] }[];
  assessment: string | null;
}

export interface Verification {
  claim: string;
  evidence: string[];
  verdict: 'supported' | 'partial' | 'unsupported';
  reason: string;
}

export interface InvestigationResult {
  question: string;
  answer: string;
  status: 'answered' | 'partial' | 'insufficient_evidence' | 'failed';
  citations: Citation[];
  contradictions: Contradiction[];
  missing: string | null;
  verification: Verification[];
  warnings: string[];
  searches: string[];
  evidence_seen: number;
  evidence_sources: string[];
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  models: string[];
  duration_ms: number;
}

/** One server-sent event from /api/investigate/stream. */
export interface AgentEvent {
  type: 'search' | 'read_more' | 'tool_error' | 'warning' | 'verification' | 'final' | 'error';
  data: any;
}

export interface DocumentSummary {
  id: string;
  source_id: string;
  title: string;
  source_type: string;
  file_format: string;
  version: number;
  chunks: number;
  created_at: string;
  updated_at: string;
  metadata: Record<string, any>;
}

export interface DocumentDetail extends DocumentSummary {
  content: string;
  content_truncated: boolean;
  versions: { version: number; title: string; replaced_at: string }[];
}

export interface IngestResponse {
  status: 'created' | 'updated' | 'unchanged' | 'duplicate';
  source_id: string;
  document_id: string | null;
  version: number | null;
  chunk_count: number;
  duplicate_of: string | null;
  near_duplicates: { source_id: string; distance: number }[];
}

export interface SearchHit {
  chunk_id: number;
  document_id: string;
  source_id: string;
  source_type: string;
  title: string;
  context: string;
  text: string;
  token_count: number;
  chunk_metadata: Record<string, any>;
  document_metadata: Record<string, any>;
  // dense_rank/score, keyword_rank/score, fused, title_match, relevance, final_rank
  scores: Record<string, number>;
}

export interface Health {
  status: string;
  database: string;
  documents: number;
  llm: string;
  embedding_model_matches_index: boolean;
}

/** Where documents came from; investigations and searches can be limited to one. */
export const SOURCE_TYPES = [
  { value: '', label: 'All documents' },
  { value: 'hotpotqa', label: 'Wikipedia (HotpotQA)' },
  { value: 'upload', label: 'Uploaded documents' }, // includes the synthetic company files
];

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  health(): Observable<Health> {
    return this.http.get<Health>('/api/health');
  }

  listDocuments(options: { sourceType?: string; q?: string; limit: number; offset: number }) {
    let params = new HttpParams().set('limit', options.limit).set('offset', options.offset);
    if (options.sourceType) params = params.set('source_type', options.sourceType);
    if (options.q) params = params.set('q', options.q);
    return this.http.get<{ total: number; items: DocumentSummary[] }>('/api/documents', { params });
  }

  getDocument(id: string) {
    return this.http.get<DocumentDetail>(`/api/documents/${id}`);
  }

  upload(file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<IngestResponse>('/api/documents', form);
  }

  replace(id: string, file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.http.put<IngestResponse>(`/api/documents/${id}`, form);
  }

  delete(id: string) {
    return this.http.delete<void>(`/api/documents/${id}`);
  }

  search(query: string, mode: string, k: number, sourceType: string) {
    const source_types = sourceType ? [sourceType] : null;
    return this.http.post<SearchHit[]>('/api/search', { query, mode, k, source_types });
  }

  /**
   * Run an investigation and receive its events as they happen (server-sent events).
   *
   * Uses fetch() and reads the stream itself rather than EventSource: EventSource can't see
   * HTTP status codes, so a 429 (rate limit) would look like a silent connection error.
   */
  investigate(question: string, sourceType: string): Observable<AgentEvent> {
    return new Observable<AgentEvent>((subscriber) => {
      const controller = new AbortController();
      const params = new URLSearchParams({ question });
      if (sourceType) params.append('source_type', sourceType);

      (async () => {
        const response = await fetch(`/api/investigate/stream?${params}`, {
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail ?? `Request failed (${response.status})`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // Events are separated by a blank line: "event: <type>\ndata: <json>\n\n"
          let boundary: number;
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const type = /^event: (.*)$/m.exec(block)?.[1];
            const data = /^data: (.*)$/m.exec(block)?.[1];
            if (type && data) subscriber.next({ type: type as AgentEvent['type'], data: JSON.parse(data) });
          }
        }
        subscriber.complete();
      })().catch((error) => {
        if (error.name !== 'AbortError') subscriber.error(error);
      });

      // Unsubscribing (leaving the page) aborts the request; the server then stops the agent.
      return () => controller.abort();
    });
  }
}
