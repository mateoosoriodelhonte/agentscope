<script setup lang="ts">
import { computed } from 'vue'
import type { CaseResult, CheckName, CheckResult, EvalCase, Evidence, TraceRecord } from '@/api/types'
import CopyButton from '@/components/ui/CopyButton.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Kbd from '@/components/ui/Kbd.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import type { Outcome } from '@/composables/useTraceModel'
import { expectedVsActual } from './checkSummary'
import ToolSequenceDiff, { executedTools, expectedTools } from './ToolSequenceDiff.vue'

/*
 * Check results, failures first. Activating a check links it to the trace: the parent
 * selects and outlines its evidence spans; `absent` evidence (something that never
 * happened, so there is no span) is shown as an expected-vs-executed tool sequence.
 */
const props = defineProps<{
  evaluation: CaseResult | null
  evalCase: EvalCase | null
  trace: TraceRecord
  activeCheck: string | null
  /** Span ids of the active check's evidence that exist in this trace, in order. */
  evidenceSpans: string[]
  selectedSpan: string | null
}>()
const emit = defineEmits<{ activate: [name: CheckName | null]; jump: [spanId: string] }>()

const ORDER = { failed: 0, passed: 1, not_applicable: 2 } as const
const checks = computed(() =>
  [...(props.evaluation?.checks ?? [])].sort((a, b) => ORDER[a.status] - ORDER[b.status]),
)
const counts = computed(() => {
  const out = { failed: 0, passed: 0, not_applicable: 0 }
  for (const check of checks.value) out[check.status] += 1
  return out
})
const position = computed(() => props.evidenceSpans.indexOf(props.selectedSpan ?? ''))

const ROLE_OUTCOMES: Record<Evidence['role'], Outcome> = {
  violation: { tone: 'fail', label: 'violation' },
  support: { tone: 'ok', label: 'support' },
  absent: { tone: 'warn', label: 'absent' },
}

function hasToolAbsence(check: CheckResult): boolean {
  return check.evidence.some((e) => e.role === 'absent' && e.tool !== null)
}

const sequence = computed(() => ({
  expected: props.evalCase ? expectedTools(props.evalCase) : [],
  actual: executedTools(props.trace),
  blocked: props.trace.tool_invocations.filter((i) => !i.executed).length,
}))

function toggle(check: CheckResult): void {
  emit('activate', props.activeCheck === check.name ? null : check.name)
}

/** An ad-hoc run can become a test case: offer the minimal EvalCase as JSON. */
const caseSnippet = computed(() => {
  const output = props.trace.final_output
  const answer =
    output && typeof output === 'object' && !Array.isArray(output) && typeof output.answer === 'string'
      ? output.answer
      : '<expected answer>'
  return JSON.stringify(
    {
      id: 'my-case',
      task: props.trace.task,
      expected: { field: 'answer', match: 'normalized', value: answer },
    },
    null,
    2,
  )
})
</script>

<template>
  <section class="verdict" aria-labelledby="verdict-title">
    <header class="head">
      <h2 id="verdict-title" class="section-title">Verdict</h2>
      <template v-if="evaluation">
        <StatusBadge :status="evaluation.passed ? 'passed' : 'failed'" />
        <span class="counts muted">
          {{ counts.failed }} failed · {{ counts.passed }} passed
          <template v-if="counts.not_applicable"> · {{ counts.not_applicable }} not applicable</template>
        </span>
      </template>
    </header>

    <EmptyState v-if="!evaluation" title="Not evaluated" variant="placeholder" compact>
      <p>This run had no expectation, so no checks ran. To judge it, save it as a test case:</p>
      <div class="snippet">
        <pre class="mono">{{ caseSnippet }}</pre>
        <CopyButton :value="caseSnippet" label="Copy test case JSON" />
      </div>
    </EmptyState>

    <template v-else>
      <p v-if="evaluation.harness_error" class="harness" role="alert">
        Harness error (not an agent failure): {{ evaluation.harness_error }}
      </p>
      <ul class="checks">
        <li v-for="check in checks" :key="check.name" :class="[check.status, { active: activeCheck === check.name }]">
          <button
            type="button"
            class="check"
            :aria-expanded="activeCheck === check.name"
            :aria-controls="`check-${check.name}`"
            @click="toggle(check)"
          >
            <StatusBadge :status="check.status" variant="plain" class="status" />
            <span class="name mono">{{ check.name }}</span>
            <span class="line" :title="expectedVsActual(check)">{{ expectedVsActual(check) }}</span>
          </button>
          <div v-if="activeCheck === check.name" :id="`check-${check.name}`" class="detail">
            <p class="message">{{ check.message }} <span class="faint mono">({{ check.reason }})</span></p>
            <p v-if="evidenceSpans.length" class="cycle muted">
              Evidence span {{ position >= 0 ? position + 1 : '–' }} of {{ evidenceSpans.length }} ·
              <Kbd>n</Kbd> / <Kbd>Shift N</Kbd> to cycle
            </p>
            <ul v-if="check.evidence.length" class="evidence">
              <li v-for="(ev, i) in check.evidence" :key="i">
                <StatusBadge :outcome="ROLE_OUTCOMES[ev.role]" variant="plain" />
                <span class="ev-detail">{{ ev.detail }}</span>
                <button
                  v-if="ev.span_id && evidenceSpans.includes(ev.span_id)"
                  type="button"
                  class="btn ghost jump"
                  :aria-current="ev.span_id === selectedSpan ? 'true' : undefined"
                  @click="emit('jump', ev.span_id)"
                >
                  Show span
                </button>
                <span v-else-if="ev.role !== 'absent'" class="faint">no span: judged on the run as a whole</span>
              </li>
            </ul>
            <p v-else class="faint">No evidence recorded for this check.</p>
            <ToolSequenceDiff
              v-if="hasToolAbsence(check)"
              :expected="sequence.expected"
              :actual="sequence.actual"
              :blocked="sequence.blocked"
            />
            <p v-if="hasToolAbsence(check) && !evalCase" class="faint">
              The test case is not available, so the expected sequence cannot be shown.
            </p>
          </div>
        </li>
      </ul>
      <p v-if="!checks.length" class="faint">No checks ran for this case.</p>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.counts {
  margin-left: auto;
  font-size: 12px;
}
.checks {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.checks > li {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-1);
}
.checks > li.failed {
  border-left: 3px solid var(--fail);
}
.checks > li.active {
  border-color: var(--accent);
}
.check {
  display: grid;
  grid-template-columns: 112px minmax(0, max-content) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.check:hover {
  background: var(--hover);
}
.name {
  color: var(--text);
}
.line {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-muted);
  font-size: 12px;
}
.not_applicable .name,
.not_applicable .line {
  color: var(--text-faint);
}
.detail {
  padding: 2px 10px 10px;
  border-top: 1px solid var(--border);
  font-size: 12.5px;
}
.message {
  margin: 8px 0 6px;
}
.cycle {
  margin: 0 0 6px;
  font-size: 12px;
}
.evidence {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.evidence li {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
}
.ev-detail {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.jump {
  height: 22px;
  margin-left: auto;
  color: var(--accent);
  font-size: 12px;
}
.jump[aria-current='true'] {
  background: var(--accent-soft);
}
.harness {
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--fail-soft);
  color: var(--fail);
  font-size: 12.5px;
}
.snippet {
  position: relative;
}
.snippet pre {
  margin: 4px 0 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-2);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.snippet :deep(.copy) {
  position: absolute;
  top: 6px;
  right: 6px;
}
</style>
