<script setup lang="ts">
import { computed } from 'vue'

/*
 * A chart marker whose shape and colour both identify the series (circle, square,
 * triangle, diamond), so identity never relies on colour alone. A 2px ring in the surface
 * colour keeps it legible where it crosses a line.
 */
const props = defineProps<{ series: number; x: number; y: number }>()
const SHAPES = ['circle', 'square', 'triangle', 'diamond'] as const
const shape = computed(() => SHAPES[props.series % SHAPES.length]!)
const R = 4.5
const triangle = computed(() => {
  const { x, y } = props
  return `${x},${y - R - 1} ${x + R + 1},${y + R} ${x - R - 1},${y + R}`
})
const diamond = computed(() => {
  const { x, y } = props
  const r = R + 1
  return `${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`
})
</script>

<template>
  <g class="marker" :class="[`s${(series % 4) + 1}`, shape]" :data-shape="shape">
    <circle v-if="shape === 'circle'" :cx="x" :cy="y" :r="R" />
    <rect v-else-if="shape === 'square'" :x="x - R" :y="y - R" :width="2 * R" :height="2 * R" rx="1" />
    <polygon v-else-if="shape === 'triangle'" :points="triangle" />
    <polygon v-else :points="diamond" />
    <slot />
  </g>
</template>

<style scoped>
.marker > * {
  stroke: var(--surface-1);
  stroke-width: 2;
  paint-order: stroke;
}
.s1 > * {
  fill: var(--series-1);
}
.s2 > * {
  fill: var(--series-2);
}
.s3 > * {
  fill: var(--series-3);
}
.s4 > * {
  fill: var(--series-4);
}
</style>
