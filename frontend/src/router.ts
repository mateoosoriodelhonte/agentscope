import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/runs' },
    { path: '/runs', name: 'runs', component: () => import('./views/RunsView.vue'), meta: { title: 'Runs' } },
    {
      path: '/runs/:runId',
      name: 'trace',
      component: () => import('./views/TraceView.vue'),
      props: true,
      meta: { title: 'Trace' },
    },
  ],
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? 'AgentScope')} · AgentScope`
})
