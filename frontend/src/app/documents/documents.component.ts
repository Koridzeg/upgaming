import { DatePipe, DecimalPipe, JsonPipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService, DocumentDetail, DocumentSummary, IngestResponse, SOURCE_TYPES } from '../api.service';

const PAGE_SIZE = 20;

@Component({
  selector: 'app-documents',
  imports: [FormsModule, DatePipe, DecimalPipe, JsonPipe],
  templateUrl: './documents.component.html',
  styleUrl: './documents.component.css',
})
export class DocumentsComponent {
  private api = inject(ApiService);

  readonly sourceTypes = SOURCE_TYPES;
  readonly pageSize = PAGE_SIZE;

  query = '';
  sourceType = 'upload'; // the 66k Wikipedia articles would bury the uploaded files
  offset = signal(0);
  total = signal(0);
  documents = signal<DocumentSummary[]>([]);
  detail = signal<DocumentDetail | null>(null);
  busy = signal(false);
  message = signal<{ text: string; error?: boolean } | null>(null);

  constructor() {
    this.load();
  }

  load(offset = 0) {
    this.offset.set(offset);
    this.api
      .listDocuments({ sourceType: this.sourceType, q: this.query.trim(), limit: PAGE_SIZE, offset })
      .subscribe({
        next: (page) => {
          this.documents.set(page.items);
          this.total.set(page.total);
        },
        error: (error) => this.fail(error),
      });
  }

  open(document: DocumentSummary) {
    this.api.getDocument(document.id).subscribe({
      next: (detail) => this.detail.set(detail),
      error: (error) => this.fail(error),
    });
  }

  upload(input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = ''; // so choosing the same file again still triggers (change)
    if (!file) return;
    this.busy.set(true);
    this.message.set({ text: `Processing ${file.name}…` });
    this.api.upload(file).subscribe({
      next: (result) => this.done(file.name, result),
      error: (error) => this.fail(error),
    });
  }

  replace(document: DocumentSummary, input: HTMLInputElement) {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    this.busy.set(true);
    this.message.set({ text: `Replacing ${document.title} with ${file.name}…` });
    this.api.replace(document.id, file).subscribe({
      next: (result) => this.done(file.name, result),
      error: (error) => this.fail(error),
    });
  }

  remove(document: DocumentSummary) {
    if (!confirm(`Delete "${document.title}" and all its chunks?`)) return;
    this.api.delete(document.id).subscribe({
      next: () => {
        this.message.set({ text: `Deleted ${document.title}.` });
        if (this.detail()?.id === document.id) this.detail.set(null);
        this.load(this.offset());
      },
      error: (error) => this.fail(error),
    });
  }

  private done(filename: string, result: IngestResponse) {
    const outcomes: Record<IngestResponse['status'], string> = {
      created: `Added ${filename}: ${result.chunk_count} chunks.`,
      updated: `Updated ${filename} to version ${result.version}: ${result.chunk_count} chunks.`,
      unchanged: `${filename} is identical to the stored version; nothing changed.`,
      duplicate: `${filename} has exactly the same content as ${result.duplicate_of}; not stored twice.`,
    };
    let text = outcomes[result.status];
    if (result.near_duplicates.length) {
      const similar = result.near_duplicates.map((d) => d.source_id).join(', ');
      text += ` Very similar to: ${similar}.`;
    }
    this.busy.set(false);
    this.message.set({ text });
    this.load(0);
  }

  private fail(error: HttpErrorResponse) {
    this.busy.set(false);
    const detail = error.error?.detail;
    this.message.set({ text: typeof detail === 'string' ? detail : error.message, error: true });
  }
}
