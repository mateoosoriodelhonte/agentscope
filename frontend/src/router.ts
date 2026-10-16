import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

export type NavKey =
  | 'overview'
  | 'runs'
  | 'evaluations'
  | 'compare'
  | 'failures'
  | 'agents'
  | 'datasets'
  | 'settings'

declare module 'vue-router' {
  interface RouteMeta {
    title: string
    /** The sidebar entry this route belongs to. */
    nav?: NavKey
    /** What a not-yet-built screen will show (rendered by PlaceholderView). */
    summary?: string
  }
}

const placeholder = () => import('./views/PlaceholderView.vue')

function screen(path: string, name: string, nav: NavKey, title: string, summary: string): RouteRecordRaw {
  return { path, name, component: placeholder, meta: { title, nav, summary } }
}

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/runs' },
  screen(
    '/overview',
    'overview',
    'overview',
    'Overview',
    'Pass rate per agent across suite runs, the latest gate result, and recent activity.',
  ),
  {
    path: '/runs',
    name: 'runs',
    component: () => import('./views/RunsView.vue'),
    meta: { title: 'Runs', nav: 'runs' },
  },
  {
    path: '/runs/:runId',
    name: 'trace',
    component: () => import('./views/TraceView.vue'),
    props: true,
    meta: { title: 'Trace', nav: 'runs' },
  },
  screen(
    '/evaluations',
    'evaluations',
    'evaluations',
    'Evaluations',
    'Suite runs against dataset versions, with a case × check matrix and the gate checklist.',
  ),
  screen(
    '/evaluations/:suiteId',
    'evaluation',
    'evaluations',
    'Evaluation',
    'One suite run: every case, every check, and the gate decision.',
  ),
  screen(
    '/evaluations/:suiteId/cases/:caseId',
    'evaluation-case',
    'evaluations',
    'Case',
    'One case of a suite run, linked to its trace.',
  ),
  screen(
    '/compare',
    'compare',
    'compare',
    'Compare',
    'Two suite runs side by side: config diff, metric deltas, and per-case regressions and fixes.',
  ),
  screen(
    '/compare/runs',
    'compare-runs',
    'compare',
    'Compare runs',
    'A step-aligned diff of two traces that names the first divergence.',
  ),
  screen(
    '/failures',
    'failures',
    'failures',
    'Failures',
    'Resilience by fault kind and agent version, and failure buckets with example runs.',
  ),
  screen('/agents', 'agents', 'agents', 'Agents', 'Agent versions, their configuration, and graph topology.'),
  screen(
    '/agents/:agent/:version',
    'agent-version',
    'agents',
    'Agent version',
    'One agent version: prompt, tools, limits, and retry policy.',
  ),
  screen('/datasets', 'datasets', 'datasets', 'Datasets', 'Dataset versions and their cases; import and lint.'),
  screen(
    '/datasets/:name/:version',
    'dataset-version',
    'datasets',
    'Dataset',
    'One dataset version and its cases.',
  ),
  screen(
    '/settings',
    'settings',
    'settings',
    'Settings',
    'Effective settings, storage inventory, every network destination, and data purge.',
  ),
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('./views/NotFoundView.vue'),
    meta: { title: 'Not found' },
  },
]

export const router = createRouter({ history: createWebHistory(), routes })
