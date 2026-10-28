import { DecimalPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { ApiService, Health } from './api.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, DecimalPipe],
  template: `
    <header>
      <div class="brand">AI Investigation Engine</div>
      <nav>
        <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Investigate</a>
        <a routerLink="/documents" routerLinkActive="active">Documents</a>
        <a routerLink="/search" routerLinkActive="active">Search explorer</a>
      </nav>
      @if (health(); as h) {
        <div class="health" [class.bad]="h.status !== 'ok'">
          {{ h.documents | number }} documents · LLM {{ h.llm }}
        </div>
      } @else {
        <div class="health bad">API unreachable</div>
      }
    </header>
    <main><router-outlet /></main>
  `,
  styles: `
    header { display: flex; align-items: center; gap: 24px; padding: 12px 24px;
             background: var(--surface); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
    .brand { font-weight: 700; font-size: 17px; }
    nav { display: flex; gap: 4px; }
    nav a { padding: 6px 12px; border-radius: 6px; color: var(--muted); text-decoration: none; }
    nav a.active { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .health { margin-left: auto; font-size: 13px; color: var(--muted); }
    .health.bad { color: var(--bad); }
    main { max-width: 1200px; margin: 0 auto; padding: 24px 16px; }
  `,
})
export class AppComponent {
  health = signal<Health | null>(null);

  constructor() {
    inject(ApiService).health().subscribe({ next: (h) => this.health.set(h), error: () => this.health.set(null) });
  }
}
