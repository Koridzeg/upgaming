import { DecimalPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService, SearchHit, SOURCE_TYPES } from '../api.service';

/** Shows what retrieval returns before any LLM is involved, with every score that ranked it. */
@Component({
  selector: 'app-search',
  imports: [FormsModule, DecimalPipe],
  templateUrl: './search.component.html',
  styleUrl: './search.component.css',
})
export class SearchComponent {
  private api = inject(ApiService);

  readonly sourceTypes = SOURCE_TYPES;
  readonly modes = [
    { value: 'hybrid', label: 'Hybrid (dense + BM25, fused and re-ranked)' },
    { value: 'dense', label: 'Dense only (embeddings)' },
    { value: 'keyword', label: 'Keyword only (BM25)' },
  ];

  query = '';
  mode = 'hybrid';
  k = 10;
  sourceType = '';
  hits = signal<SearchHit[] | null>(null);
  error = signal<string | null>(null);
  elapsed = signal(0);

  run() {
    if (!this.query.trim()) return;
    const started = performance.now();
    this.error.set(null);
    this.api.search(this.query.trim(), this.mode, this.k, this.sourceType).subscribe({
      next: (hits) => {
        this.hits.set(hits);
        this.elapsed.set(performance.now() - started);
      },
      error: (error: HttpErrorResponse) => this.error.set(error.error?.detail ?? error.message),
    });
  }
}
