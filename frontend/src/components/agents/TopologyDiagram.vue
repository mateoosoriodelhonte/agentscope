<script setup lang="ts">
import { computed, useId } from 'vue'
import type { GraphVariant } from '@/api/types'

/*
 * The two graph topologies side by side, from runtime/graph.py `topology()`: the basic
 * plan → guard → act → observe loop, and the validated graph, which adds an output
 * validation node with a bounded repair loop. Static on purpose (no per-trace graph);
 * the table view lists every edge with what it means.
 */
const props = defineProps<{ current: GraphVariant; view: 'chart' | 'table' }>()
const arrow = useId()
const added = useId()

interface Node {
  id: string
  x: number
  y: number
  terminal?: boolean
  only?: GraphVariant
}
interface Edge {
  from: string
  to: string
  meaning: string
  /** Panel-local SVG path. Edges with a variant-specific shape have one per variant. */
  path: string | Record<GraphVariant, string>
  label?: { text: string; x: number; y: number }
  only?: GraphVariant
}

const NODES: Node[] = [
  { id: 'START', x: 24, y: 52, terminal: true },
  { id: 'plan', x: 100, y: 52 },
  { id: 'guard', x: 190, y: 52 },
  { id: 'act', x: 276, y: 52 },
  { id: 'observe', x: 190, y: 124 },
  { id: 'validate', x: 100, y: 124, only: 'validated' },
  { id: 'finish', x: 100, y: 196 },
  { id: 'END', x: 190, y: 196, terminal: true },
]

const EDGES: Edge[] = [
  { from: 'START', to: 'plan', meaning: 'the run starts', path: 'M42,52 L66,52' },
  { from: 'plan', to: 'guard', meaning: 'the model requested a tool call', path: 'M132,52 L156,52' },
  {
    from: 'plan',
    to: 'plan',
    meaning: 'the response was not a valid decision: ask the model to correct it',
    path: 'M90,39 C82,12 118,12 110,39',
  },
  {
    from: 'plan',
    to: 'finish',
    meaning: 'basic: a final answer, or the run must stop · validated: the run must stop (step budget or deadline)',
    path: { basic: 'M100,65 L100,181', validated: 'M68,56 C30,92 30,168 66,192' },
  },
  { from: 'guard', to: 'act', meaning: 'the call is permitted', path: 'M222,52 L242,52' },
  {
    from: 'guard',
    to: 'observe',
    meaning: 'the call is denied; the model observes the denial',
    path: 'M190,65 L190,109',
    label: { text: 'denied', x: 196, y: 92 },
  },
  { from: 'act', to: 'observe', meaning: 'the tool result, or the injected fault, is observed', path: 'M266,65 L218,111' },
  { from: 'observe', to: 'plan', meaning: 'the next step', path: 'M160,114 L122,66' },
  {
    from: 'plan',
    to: 'validate',
    meaning: 'a final answer is checked against the output schema',
    path: 'M100,65 L100,109',
    only: 'validated',
  },
  {
    from: 'validate',
    to: 'finish',
    meaning: 'the output is valid, or the repair budget is spent',
    path: 'M100,137 L100,181',
    only: 'validated',
  },
  {
    from: 'validate',
    to: 'plan',
    meaning: 'the output is invalid: request a repair',
    path: 'M78,112 C66,98 66,80 78,66',
    label: { text: 'repair', x: 34, y: 92 },
    only: 'validated',
  },
  { from: 'finish', to: 'END', meaning: 'the run ends', path: 'M132,196 L170,196' },
]

const PANELS: { variant: GraphVariant; x: number }[] = [
  { variant: 'basic', x: 0 },
  { variant: 'validated', x: 340 },
]

const nodesOf = (variant: GraphVariant) => NODES.filter((n) => !n.only || n.only === variant)
const edgesOf = (variant: GraphVariant) => EDGES.filter((e) => !e.only || e.only === variant)
const pathOf = (edge: Edge, variant: GraphVariant) => (typeof edge.path === 'string' ? edge.path : edge.path[variant])
const size = (node: Node) => (node.terminal ? { w: 36, h: 20 } : { w: 64, h: 26 })
const summary = computed(
  () =>
    `Graph topologies. Basic: ${edgesOf('basic').length} edges. Validated adds a validate node with ${
      EDGES.filter((e) => e.only === 'validated').length
    } edges. This version uses the ${props.current} graph.`,
)
</script>

<template>
  <figure class="topology">
    <svg v-if="view === 'chart'" viewBox="0 0 660 246" role="img" :aria-label="summary">
      <defs>
        <marker :id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" class="head" />
        </marker>
        <marker :id="added" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L8,4 L0,8 z" class="head added" />
        </marker>
      </defs>
      <g v-for="panel in PANELS" :key="panel.variant" :transform="`translate(${panel.x} 0)`" :class="{ current: panel.variant === current }">
        <rect class="frame" x="2" y="2" width="318" height="242" rx="8" />
        <text class="title" x="14" y="20">
          {{ panel.variant }}{{ panel.variant === current ? ' · this version' : '' }}
        </text>
        <!-- The graph sits below the title, clear of plan's self-loop. -->
        <g transform="translate(0 18)">
          <g v-for="edge in edgesOf(panel.variant)" :key="`${edge.from}-${edge.to}`" class="edge" :class="{ added: edge.only }">
            <path :d="pathOf(edge, panel.variant)" :marker-end="`url(#${edge.only ? added : arrow})`">
              <title>{{ edge.from }} → {{ edge.to }}: {{ edge.meaning }}</title>
            </path>
            <text v-if="edge.label" class="edge-label" :x="edge.label.x" :y="edge.label.y">{{ edge.label.text }}</text>
          </g>
          <g v-for="node in nodesOf(panel.variant)" :key="node.id" class="node" :class="{ terminal: node.terminal, added: node.only }">
            <rect :x="node.x - size(node).w / 2" :y="node.y - size(node).h / 2" :width="size(node).w" :height="size(node).h" :rx="node.terminal ? 10 : 5" />
            <text :x="node.x" :y="node.y" dy="0.34em" text-anchor="middle">{{ node.id }}</text>
          </g>
        </g>
      </g>
    </svg>
    <div v-else class="table-wrap panel">
      <table class="data-table">
        <caption class="visually-hidden">Edges of the basic and validated graphs</caption>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">To</th>
            <th scope="col">Basic</th>
            <th scope="col">Validated</th>
            <th scope="col">Meaning</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="edge in EDGES" :key="`${edge.from}-${edge.to}`">
            <td class="mono">{{ edge.from }}</td>
            <td class="mono">{{ edge.to }}</td>
            <td>{{ edge.only === 'validated' ? '—' : 'yes' }}</td>
            <td>yes</td>
            <td class="muted">{{ edge.meaning }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <figcaption class="faint">
      Additions in the validated graph are drawn in the accent colour with a heavier outline.
    </figcaption>
  </figure>
</template>

<style scoped>
.topology {
  display: grid;
  gap: 6px;
  margin: 0;
}
svg {
  width: 100%;
  max-width: 760px;
  height: auto;
}
.frame {
  fill: var(--surface-1);
  stroke: var(--border);
}
.current .frame {
  stroke: var(--accent);
  stroke-width: 1.5;
}
.title {
  fill: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.current .title {
  fill: var(--text);
}
.edge path {
  fill: none;
  stroke: var(--text-faint);
  stroke-width: 1.4;
}
.edge.added path {
  stroke: var(--accent);
  stroke-width: 2;
}
.head {
  fill: var(--text-faint);
}
.head.added {
  fill: var(--accent);
}
.edge-label {
  fill: var(--text-muted);
  font-size: 10px;
}
.node rect {
  fill: var(--surface-2);
  stroke: var(--border-strong);
}
.node.terminal rect {
  fill: var(--surface-1);
}
.node.added rect {
  fill: var(--accent-soft);
  stroke: var(--accent);
  stroke-width: 2;
}
.node text {
  fill: var(--text);
  font-family: var(--font-mono);
  font-size: 11px;
}
.node.terminal text {
  fill: var(--text-muted);
  font-size: 9.5px;
}
figcaption {
  font-size: 12px;
}
</style>
