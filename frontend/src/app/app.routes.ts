import { Routes } from '@angular/router';

import { DocumentsComponent } from './documents/documents.component';
import { InvestigateComponent } from './investigate/investigate.component';
import { SearchComponent } from './search/search.component';

export const routes: Routes = [
  { path: '', component: InvestigateComponent, title: 'Investigate · AI Investigation Engine' },
  { path: 'documents', component: DocumentsComponent, title: 'Documents · AI Investigation Engine' },
  { path: 'search', component: SearchComponent, title: 'Search · AI Investigation Engine' },
  { path: '**', redirectTo: '' },
];
