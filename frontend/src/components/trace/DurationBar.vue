<script lang="ts">
import type { Tone } from '@/composables/useTraceModel'

export type BarMode = 'relative' | 'timeline'

export interface BarInput {
  mode: BarMode
  startMs: number
  durationMs: number
  /** Run duration: the timeline axis. */
  totalMs: number
  /** Longest row: the relative scale. */
  scaleMs: number
  /** Tool timeout; drawn as a tick when it falls inside the scale. */
  tickMs: number | null
  /** Timeline mode only: draw the tick at start + timeout (tool spans). */
  timelineTick: boolean
}

export interface BarGeometry {
  left: number
  width: number
  tick: number | null
}

const pct = (value: number, of: number) => (of > 0 ? Math.min(100, Math.max(0, (value / of) * 100)) : 0)

/**
 * Relative: every bar starts at 0 and is scaled to the longest row, so sub-millisecond
 * fake-model spans stay comparable. Timeline: offset and width on the run's real time axis.
 * Percentages; the minimum visible width (2px) is applied in CSS.
 */
export function barGeometry(input: BarInput): BarGeometry {
  if (input.mode === 'relative') {
    const tick = input.tickMs !== null && input.tickMs <= input.scaleMs ? pct(input.tickMs, input.scaleMs) : null
    return { left: 0, width: pct(input.durationMs, input.scaleMs), tick }
  }
  const left = pct(input.startMs, input.totalMs)
  const width = Math.min(pct(input.durationMs, input.totalMs), 100 - left)
  const end = input.tickMs === null ? null : input.startMs + input.tickMs
  const tick = input.timelineTick && end !== null && end <= input.totalMs ? pct(end, input.totalMs) : null
  return { left, width, tick }
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<BarInput & { fault?: boolean; tone?: Tone }>(), {
  fault: false,
  tone: 'neutral',
})
const geometry = computed(() => barGeometry(props))
</script>

<template>
  <div class="track" aria-hidden="true">
    <div
      class="fill"
      :class="[tone, { hatched: fault }]"
      :style="{ left: `${geometry.left}%`, width: `${geometry.width}%` }"
    ></div>
    <div v-if="geometry.tick !== null" class="tick" :style="{ left: `${geometry.tick}%` }"></div>
  </div>
</template>

<style scoped>
.track {
  position: relative;
  height: 10px;
  border-radius: 2px;
  background: var(--bar-track);
}
.fill {
  position: absolute;
  top: 0;
  bottom: 0;
  min-width: 2px;
  border-radius: 2px;
  background: var(--bar);
}
.fill.fail {
  background: var(--fail);
}
.fill.hatched {
  background: var(--injected-soft) var(--hatch);
  box-shadow: inset 0 0 0 1px var(--injected);
}
.tick {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 0;
  border-left: 1px dashed var(--fail);
}
</style>
