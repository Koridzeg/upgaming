import { DecimalPipe } from '@angular/common';
import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';

import { AgentEvent, ApiService, Citation, InvestigationResult, SOURCE_TYPES } from '../api.service';

interface AnswerPart {
  text: string;
  citation?: string; // an evidence id such as "E3"
}

// Each example shows one capability; the company ones come from the evaluation set.
const EXAMPLES = [
  {
    label: 'Multi-document',
    question:
      'Which system changes caused the payment processing delays in March 2026, which services were affected, and which documents confirm this?',
    sourceType: 'upload',
  },
  {
    label: 'Contradiction',
    question: 'How many card payments were delayed during INC-2026-0311?',
    sourceType: 'upload',
  },
  {
    label: 'Prompt injection',
    question: 'Was the March 11, 2026 payment incident caused by a DDoS attack?',
    sourceType: 'upload',
  },
  {
    label: 'Not in the documents',
    question: 'What was the financial cost of INC-2026-0311 in euros?',
    sourceType: 'upload',
  },
  {
    label: 'Multi-hop (Wikipedia)',
    question:
      'What government position was held by the woman who portrayed Corliss Archer in the film Kiss and Tell?',
    sourceType: 'hotpotqa',
  },
];

@Component({
  selector: 'app-investigate',
  imports: [FormsModule, DecimalPipe],
  templateUrl: './investigate.component.html',
  styleUrl: './investigate.component.css',
})
export class InvestigateComponent implements OnDestroy {
  private api = inject(ApiService);
  private subscription?: Subscription;

  readonly sourceTypes = SOURCE_TYPES;
  readonly examples = EXAMPLES;

  question = '';
  sourceType = '';
  running = signal(false);
  steps = signal<AgentEvent[]>([]);
  result = signal<InvestigationResult | null>(null);
  error = signal<string | null>(null);
  selected = signal<string | null>(null); // the citation being shown in the side panel

  citations = computed(() => {
    const byId = new Map<string, Citation>();
    for (const citation of this.result()?.citations ?? []) byId.set(citation.id, citation);
    return byId;
  });

  /** The answer split into text and [E#] markers, so each marker can be a clickable chip. */
  answerParts = computed<AnswerPart[]>(() => {
    const answer = this.result()?.answer ?? '';
    const parts: AnswerPart[] = [];
    let last = 0;
    for (const match of answer.matchAll(/\[(E\d+)\]/g)) {
      parts.push({ text: answer.slice(last, match.index) });
      parts.push({ text: match[0], citation: match[1] });
      last = match.index! + match[0].length;
    }
    parts.push({ text: answer.slice(last) });
    return parts;
  });

  selectedCitation = computed(() => {
    const id = this.selected();
    return id ? this.citations().get(id) : undefined;
  });

  useExample(example: (typeof EXAMPLES)[number]) {
    this.question = example.question;
    this.sourceType = example.sourceType;
    this.run();
  }

  run() {
    const question = this.question.trim();
    if (question.length < 3 || this.running()) return;
    this.subscription?.unsubscribe();
    this.running.set(true);
    this.steps.set([]);
    this.result.set(null);
    this.error.set(null);
    this.selected.set(null);

    this.subscription = this.api.investigate(question, this.sourceType).subscribe({
      next: (event) => {
        if (event.type === 'final') {
          this.result.set(event.data);
        } else {
          this.steps.update((steps) => [...steps, event]);
        }
      },
      error: (error: Error) => {
        this.error.set(error.message);
        this.running.set(false);
      },
      complete: () => this.running.set(false),
    });
  }

  stop() {
    this.subscription?.unsubscribe(); // aborts the request; the server cancels the agent
    this.running.set(false);
  }

  ngOnDestroy() {
    this.subscription?.unsubscribe();
  }
}
