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
  }
}

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/overview' },
  {
    path: '/overview',
    name: 'overview',
    component: () => import('./views/OverviewView.vue'),
    meta: { title: 'Overview', nav: 'overview' },
  },
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
  {
    path: '/evaluations',
    name: 'evaluations',
    component: () => import('./views/EvaluationsView.vue'),
    meta: { title: 'Evaluations', nav: 'evaluations' },
  },
  {
    path: '/evaluations/:suiteId',
    name: 'evaluation',
    component: () => import('./views/EvaluationView.vue'),
    props: true,
    meta: { title: 'Evaluation', nav: 'evaluations' },
  },
  {
    path: '/evaluations/:suiteId/cases/:caseId',
    name: 'evaluation-case',
    component: () => import('./views/EvaluationCaseView.vue'),
    props: true,
    meta: { title: 'Case', nav: 'evaluations' },
  },
  {
    path: '/compare',
    name: 'compare',
    component: () => import('./views/CompareView.vue'),
    meta: { title: 'Compare', nav: 'compare' },
  },
  {
    path: '/compare/runs',
    name: 'compare-runs',
    component: () => import('./views/CompareRunsView.vue'),
    meta: { title: 'Compare runs', nav: 'compare' },
  },
  {
    path: '/failures',
    name: 'failures',
    component: () => import('./views/FailuresView.vue'),
    meta: { title: 'Failures', nav: 'failures' },
  },
  {
    path: '/agents',
    name: 'agents',
    component: () => import('./views/AgentsView.vue'),
    meta: { title: 'Agents', nav: 'agents' },
  },
  {
    path: '/agents/:agent/:version',
    name: 'agent-version',
    component: () => import('./views/AgentVersionView.vue'),
    props: true,
    meta: { title: 'Agent version', nav: 'agents' },
  },
  {
    path: '/datasets',
    name: 'datasets',
    component: () => import('./views/DatasetsView.vue'),
    meta: { title: 'Datasets', nav: 'datasets' },
  },
  {
    path: '/datasets/:name/:version',
    name: 'dataset-version',
    component: () => import('./views/DatasetView.vue'),
    props: true,
    meta: { title: 'Dataset', nav: 'datasets' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/SettingsView.vue'),
    meta: { title: 'Settings', nav: 'settings' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('./views/NotFoundView.vue'),
    meta: { title: 'Not found' },
  },
]

export const router = createRouter({ history: createWebHistory(), routes })
