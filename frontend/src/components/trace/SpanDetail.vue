<script setup lang="ts">
import { computed } from 'vue'
import type { CaseResult, JsonValue, SpanRecord, TraceRecord } from '@/api/types'
import CopyButton from '@/components/ui/CopyButton.vue'
import JsonView from '@/components/ui/JsonView.vue'
import Metric from '@/components/ui/Metric.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import Tabs from '@/components/ui/Tabs.vue'
import TruncatedNotice from '@/components/ui/TruncatedNotice.vue'
import { formatDuration, formatTimestamp } from '@/composables/format'
import {
  attr,
  attrString,
  ATTR,
  decisionOf,
  epochMs,
  nodeName,
  runStatusOutcome,
  toolOutcome,
  type Outcome,
  type TraceModel,
  type TraceRow,
} from '@/composables/useTraceModel'
import InjectedFaultBanner from './InjectedFaultBanner.vue'
import MessageList from './MessageList.vue'
import { detailTabs } from './spanTabs'

const props = defineProps<{
  row: TraceRow
  model: TraceModel
  trace: TraceRecord
  evaluation: CaseResult | null
}>()
const tab = defineModel<string | null>('tab', { required: true })
const emit = defineEmits<{ select: [rowId: string] }>()

const tabs = computed(() => detailTabs(props.row))
const active = computed({
  get: () => tabs.value.find((t) => t.id === tab.value)?.id ?? tabs.value[0]!.id,
  set: (id: string) => (tab.value = id),
})

type JsonRecord = { [key: string]: JsonValue }
const asRecord = (value: JsonValue | undefined): JsonRecord =>
  value && typeof value === 'object' && !Array.isArray(value) ? value : {}

const span = computed(() => props.row.span)
const group = computed(() => props.model.groups.find((g) => g.id === props.row.groupId) ?? null)
const childrenOf = (parent: SpanRecord | null) =>
  parent ? props.trace.spans.filter((s) => s.parent_span_id === parent.span_id) : []
const node = computed(() => (span.value && props.row.kind === 'node' ? nodeName(span.value) : null))

const chat = computed(() => {
  if (props.row.kind === 'llm') return span.value
  const plan =
    node.value === 'plan'
      ? span.value
      : node.value === 'validate'
        ? props.trace.spans.find(
            (s) => s.kind === 'node' && nodeName(s) === 'plan' && props.model.rowById.get(s.span_id)?.groupId === props.row.groupId,
          ) ?? null
        : null
  return childrenOf(plan).find((s) => s.kind === 'llm') ?? null
})
const decision = computed(() => decisionOf(chat.value))

const stepInvocation = computed(() =>
  group.value?.kind === 'step' ? (props.trace.tool_invocations.find((i) => i.step === group.value?.index) ?? null) : null,
)
const toolSpans = computed(() =>
  props.row.kind === 'tool' && span.value
    ? [span.value]
    : node.value === 'act'
      ? childrenOf(span.value).filter((s) => s.kind === 'tool')
      : [],
)
const invocation = computed(() => {
  const callId = attrString(toolSpans.value[0], ATTR.callId)
  return callId ? (props.trace.tool_invocations.find((i) => i.call_id === callId) ?? null) : stepInvocation.value
})
const guardrail = computed(() =>
  props.row.kind === 'guardrail' ? span.value : (childrenOf(span.value).find((s) => s.kind === 'guardrail') ?? null),
)

/** What the tool returned: one attempt for a tool span, the final result for an act node. */
const result = computed(() => {
  if (props.row.kind === 'tool' && span.value) {
    const out = asRecord(span.value.output)
    return { outcome: String(out.outcome ?? 'unknown'), output: out.output ?? null, error: out.error ?? null }
  }
  const inv = invocation.value
  if (!inv) return null
  return {
    outcome: inv.outcome,
    output: inv.output,
    error: inv.attempts[inv.attempts.length - 1]?.error ?? inv.denial_reason,
  }
})

const faultKind = computed(() => {
  if (props.row.fault) return props.row.fault
  if (node.value === 'act') return invocation.value?.attempts.find((a) => a.fault)?.fault ?? null
  return attrString(chat.value, ATTR.modelFault)
})

const citations = computed(() =>
  (props.evaluation?.checks ?? []).flatMap((check) =>
    check.evidence
      .filter((e) => span.value && e.span_id === span.value.span_id)
      .map((e) => ({ check: check.name, role: e.role, detail: e.detail })),
  ),
)

/** Python's html.escape(text, quote=True), used by the runtime for frame attributes. */
const escapeAttr = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;')

/** The tool message the model saw for this step's call, found in later model inputs. */
const observation = computed(() => {
  const inv = stepInvocation.value
  if (!inv) return null
  // Match the exact header the runtime writes (src/agentscope/runtime/prompts.py), so tool
  // output that merely mentions this call id cannot be mistaken for its observation.
  const headers = [
    `Result of ${inv.tool} (call ${inv.call_id}): `,
    `<tool_output tool="${escapeAttr(inv.tool)}" call_id="${escapeAttr(inv.call_id)}" `,
  ]
  for (const s of props.trace.spans) {
    if (s.kind !== 'llm') continue
    const messages = asRecord(s.input).messages
    if (!Array.isArray(messages)) continue
    const found = messages.find((m) => {
      const record = asRecord(m)
      const content = record.content
      return record.role === 'tool' && typeof content === 'string' && headers.some((h) => content.startsWith(h))
    })
    if (found) return found
  }
  return null
})

const validation = computed(() => {
  if (node.value !== 'validate' || !span.value) return null
  const id = span.value.span_id
  const invalid = props.model.rows.filter(
    (r) => r.span && r.kind === 'node' && nodeName(r.span) === 'validate' && attr(r.span, ATTR.outputValid) === false,
  )
  const errors = props.trace.guardrail_events
    .filter((g) => g.kind === 'output_invalid' && g.span_id === id)
    .flatMap((g) => g.detail.split('; '))
  return {
    valid: attr(span.value, ATTR.outputValid) !== false,
    errors,
    repaired: attrString(span.value, ATTR.route) === 'plan',
    repairIndex: invalid.findIndex((r) => r.id === id) + 1,
    repairedAfter: props.trace.guardrail_events.find((g) => g.kind === 'output_repaired' && g.step === group.value?.index),
  }
})

const guardVerdict = computed(() => {
  const g = guardrail.value
  if (g && attr(g, ATTR.allowed) === false) {
    const out = asRecord(g.output)
    const input = asRecord(g.input)
    return { allowed: false, reason: String(out.reason ?? ''), tool: String(input.tool ?? ''), arguments: input.arguments ?? {} }
  }
  const reason = group.value?.transitions.find((t) => t.source === 'guard')?.reason ?? 'permitted'
  return {
    allowed: true,
    reason,
    tool: stepInvocation.value?.tool ?? '',
    arguments: (stepInvocation.value?.arguments ?? {}) as JsonValue,
  }
})

const usage = computed(() => {
  const input = attr(chat.value, 'gen_ai.usage.input_tokens')
  const output = attr(chat.value, 'gen_ai.usage.output_tokens')
  return typeof input === 'number' && typeof output === 'number' ? `${input} in / ${output} out` : null
})

const offset = (iso: string) => formatDuration(Math.max(0, epochMs(iso) - epochMs(props.trace.started_at)))
const truncated = computed(() => attr(span.value, ATTR.truncated) === true)
const finishOutcome = computed<Outcome>(() => runStatusOutcome(props.trace.status))
</script>

<template>
  <section class="detail" aria-labelledby="detail-title">
    <header class="head">
      <h2 id="detail-title" class="title">
        <span :class="{ mono: row.level > 1 }">{{ row.label }}</span>
        <span v-if="row.kind !== 'step' && row.kind !== 'finish'" class="kind">{{ row.kind }}</span>
      </h2>
      <StatusBadge :outcome="row.outcome" />
      <span class="duration mono">{{ formatDuration(row.durationMs) }}</span>
      <span v-if="span" class="span-id mono" :title="`Span id ${span.span_id}`">
        {{ span.span_id }}<CopyButton :value="span.span_id" label="Copy span id" />
      </span>
    </header>
    <p v-if="row.narrative" class="narrative">{{ row.narrative }}</p>
    <p v-if="citations.length" class="citations">
      Cited by
      <span v-for="(c, i) in citations" :key="i" class="citation">
        <span class="mono">{{ c.check }}</span> ({{ c.role }}: {{ c.detail }})
      </span>
    </p>
    <InjectedFaultBanner v-if="faultKind" :kind="faultKind" />
    <TruncatedNotice v-if="truncated" />

    <Tabs v-model="active" :tabs="tabs" label="Span detail">
      <template #default="{ active: current }">
        <!-- plan / chat -->
        <div v-if="current === 'decision'" class="pane">
          <p v-if="!chat" class="muted">
            No model call: {{ group?.transitions.find((t) => t.source === 'plan')?.reason ?? 'the step ended before the model was asked' }}.
          </p>
          <template v-else>
            <dl class="facts">
              <Metric label="Model" :value="String(attr(chat, 'gen_ai.request.model') ?? trace.model)" />
              <Metric label="Provider" :value="String(attr(chat, 'gen_ai.provider.name') ?? trace.provider)" />
              <Metric label="Tokens" :value="usage" missing="not reported" hint="Only provider-reported token counts are shown; nothing is estimated." />
              <Metric label="Latency" :value="formatDuration(chat.duration_ms)" />
            </dl>
            <template v-if="decision?.value">
              <h3 class="sub">
                {{ decision.value.type === 'tool_call' ? `Tool call: ${String(decision.value.tool)}` : decision.value.type === 'final' ? 'Final answer' : 'Decision' }}
              </h3>
              <JsonView
                :value="decision.value.type === 'tool_call' ? (decision.value.arguments ?? null) : (decision.value.output ?? decision.value)"
                label="Decision payload"
              />
              <p v-if="typeof decision.value.rationale === 'string'" class="rationale">
                <span class="muted">Rationale:</span> {{ decision.value.rationale }}
              </p>
            </template>
            <template v-else>
              <h3 class="sub">Not a valid decision</h3>
              <pre class="raw-text mono">{{ decision?.raw ?? '(empty response)' }}</pre>
            </template>
          </template>
        </div>

        <div v-else-if="current === 'messages'" class="pane">
          <p class="muted small">
            Exactly what the model received.
            <template v-if="trace.config.isolate_untrusted">Tool output is wrapped in untrusted <code>&lt;tool_output&gt;</code> frames.</template>
            <template v-else>Tool output is inserted inline, without untrusted frames (isolation is off).</template>
          </p>
          <MessageList v-if="chat" :messages="asRecord(chat.input).messages ?? null" />
          <p v-else class="muted">No model call in this step.</p>
        </div>

        <!-- guard -->
        <div v-else-if="current === 'verdict'" class="pane">
          <p class="verdict-line">
            <StatusBadge :outcome="guardVerdict.allowed ? { tone: 'ok', label: 'permitted' } : { tone: 'warn', label: 'blocked' }" />
            <span class="mono">{{ guardVerdict.tool }}</span>
          </p>
          <p>{{ guardVerdict.reason }}</p>
          <p v-if="!guardVerdict.allowed" class="muted small">Blocked calls never execute; the model is told the call was refused.</p>
          <h3 class="sub">Arguments</h3>
          <JsonView :value="guardVerdict.arguments" label="Tool call arguments" />
        </div>

        <div v-else-if="current === 'findings'" class="pane">
          <p class="muted small">
            Instruction-like content found in tool output. Detection is informational: the permission guard alone decides what executes.
          </p>
          <JsonView :value="span?.output ?? null" label="Scanner findings" />
        </div>

        <!-- act / tool -->
        <div v-else-if="current === 'call'" class="pane">
          <template v-if="invocation">
            <dl class="facts">
              <Metric label="Tool" :value="invocation.tool" />
              <Metric label="Call id" :value="invocation.call_id" />
              <Metric label="Permitted" :value="invocation.permitted ? 'yes' : 'no'" />
              <Metric label="Executed" :value="invocation.executed ? 'yes' : 'no'" />
            </dl>
            <h3 class="sub">Arguments</h3>
            <JsonView :value="invocation.arguments" label="Tool call arguments" />
          </template>
          <JsonView v-else :value="span?.input ?? null" label="Tool call arguments" />
        </div>

        <div v-else-if="current === 'result'" class="pane">
          <template v-if="result">
            <p class="verdict-line">
              <StatusBadge :outcome="toolOutcome(result.outcome)" />
              <span v-if="result.error" class="error-text">{{ result.error }}</span>
            </p>
            <h3 class="sub">Output</h3>
            <JsonView :value="result.output" label="Tool output" />
          </template>
          <p v-else class="muted">No result recorded.</p>
        </div>

        <div v-else-if="current === 'attempts'" class="pane">
          <table v-if="invocation?.attempts.length" class="attempts">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Outcome</th>
                <th scope="col">Fault</th>
                <th scope="col" class="num">Duration</th>
                <th scope="col">Error</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in invocation.attempts" :key="a.attempt" :class="{ current: a.span_id === span?.span_id }">
                <td class="mono">
                  <button v-if="a.span_id" type="button" class="link" @click="emit('select', a.span_id)">{{ a.attempt }}</button>
                  <template v-else>{{ a.attempt }}</template>
                </td>
                <td><StatusBadge :outcome="toolOutcome(a.outcome)" variant="plain" /></td>
                <td><span v-if="a.fault" class="fault-chip">injected {{ a.fault }}</span><span v-else class="faint">—</span></td>
                <td class="num mono">{{ formatDuration(a.duration_ms) }}</td>
                <td class="error-cell">{{ a.error ?? '' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">The call was blocked before execution, so there were no attempts.</p>
          <p v-if="invocation && invocation.attempts.length > 1" class="muted small">
            Runtime retries (retry policy: up to {{ trace.config.retry.max_tool_retries }}, on {{ trace.config.retry.retry_on.join(', ') }}).
          </p>
        </div>

        <!-- observe -->
        <div v-else-if="current === 'observation'" class="pane">
          <p class="muted small">
            {{ attr(span, ATTR.delimited) ? 'Added to the context inside an untrusted frame.' : 'Added to the context inline, without an untrusted frame.' }}
          </p>
          <MessageList v-if="observation" :messages="[observation]" />
          <p v-else class="muted">The model never saw this result (the run ended first).</p>
        </div>

        <!-- validate -->
        <div v-else-if="current === 'validation' && validation" class="pane">
          <p class="verdict-line">
            <StatusBadge :outcome="validation.valid ? { tone: 'ok', label: 'valid' } : { tone: 'fail', label: 'invalid' }" />
            <span v-if="validation.valid">Final output matches the schema<template v-if="validation.repairedAfter"> ({{ validation.repairedAfter.detail }})</template>.</span>
            <span v-else-if="validation.repaired">Repair {{ validation.repairIndex }} of {{ trace.config.retry.max_output_repairs }} requested.</span>
            <span v-else>Repair budget exhausted; the run failed.</span>
          </p>
          <template v-if="validation.errors.length">
            <h3 class="sub">Schema errors</h3>
            <ul class="errors mono">
              <li v-for="(e, i) in validation.errors" :key="i">{{ e }}</li>
            </ul>
            <p class="muted small">Repair feedback names the location and the rule only, never the offending value.</p>
          </template>
          <h3 class="sub">Candidate output</h3>
          <JsonView :value="decision?.value?.output ?? null" label="Candidate output" />
        </div>

        <!-- finish / run / step -->
        <div v-else-if="current === 'outcome'" class="pane">
          <p class="verdict-line">
            <StatusBadge :outcome="finishOutcome" />
            <span v-if="trace.error_code" class="mono">{{ trace.error_code }}</span>
          </p>
          <p v-if="trace.error" class="error-text">{{ trace.error }}</p>
          <h3 class="sub">Final output</h3>
          <JsonView :value="trace.final_output" label="Final output" />
        </div>

        <div v-else-if="current === 'run'" class="pane">
          <h3 class="sub">Task</h3>
          <p class="task">{{ trace.task }}</p>
          <h3 class="sub">Final output</h3>
          <JsonView :value="trace.final_output" label="Final output" />
          <h3 class="sub">Configuration</h3>
          <JsonView :value="trace.config as unknown as JsonValue" label="Agent configuration" />
        </div>

        <div v-else-if="current === 'step' && group" class="pane">
          <h3 class="sub">Transitions</h3>
          <ol class="transitions">
            <li v-for="t in group.transitions" :key="t.seq">
              <span class="mono edge">{{ t.source }} → {{ t.target }}</span>
              <span class="reason">{{ t.reason }}</span>
            </li>
          </ol>
          <p class="muted small">Expand the step (→) to see its spans.</p>
        </div>

        <!-- every span -->
        <div v-else-if="current === 'raw' && span" class="pane">
          <dl class="facts">
            <Metric label="Kind" :value="span.kind" />
            <Metric label="Status" :value="span.status" />
            <Metric label="Start" :value="`+${offset(span.start_time)}`" :hint="formatTimestamp(span.start_time)" />
            <Metric label="Parent" :value="span.parent_span_id ?? 'none (root)'" />
          </dl>
          <p v-if="span.status_message" class="error-text">{{ span.status_message }}</p>
          <h3 class="sub">Attributes</h3>
          <JsonView :value="span.attributes" label="Span attributes" />
          <h3 class="sub">Input</h3>
          <JsonView :value="span.input" label="Span input" />
          <h3 class="sub">Output</h3>
          <JsonView :value="span.output" label="Span output" />
          <template v-if="span.events.length">
            <h3 class="sub">Events</h3>
            <ul class="events">
              <li v-for="(e, i) in span.events" :key="i">
                <span class="mono">{{ e.name }}</span> <span class="faint mono">+{{ offset(e.timestamp) }}</span>
                <JsonView :value="e.attributes" :label="`Event ${e.name} attributes`" />
              </li>
            </ul>
          </template>
        </div>
      </template>
    </Tabs>
  </section>
</template>

<style scoped>
.detail {
  font-size: 13px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.title {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 14px;
}
.kind {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
}
.duration {
  color: var(--text-muted);
}
.span-id {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  color: var(--text-faint);
  font-size: 11px;
}
.narrative {
  margin: 6px 0 0;
  color: var(--text-muted);
}
.citations {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}
.citation {
  margin-left: 4px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
}
.detail :deep(.tabs) {
  margin-top: 10px;
}
.pane > p {
  margin: 0 0 8px;
}
.small {
  font-size: 12px;
}
.sub {
  margin: 14px 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}
.facts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px 16px;
  margin: 0 0 4px;
}
.verdict-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.error-text {
  color: var(--fail);
}
.rationale {
  margin-top: 8px;
}
.raw-text {
  margin: 0;
  padding: 8px 10px;
  border: 1px dashed var(--fail);
  border-radius: var(--radius);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.attempts {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.attempts th,
.attempts td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border);
  text-align: left;
}
.attempts th {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-muted);
}
.attempts .num {
  text-align: right;
}
.attempts tr.current {
  background: var(--selected);
}
.error-cell {
  color: var(--text-muted);
}
.fault-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--injected-soft);
  color: var(--injected);
  font-size: 11px;
}
.fault-chip::before {
  content: '';
  width: 8px;
  height: 10px;
  background: var(--hatch);
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
}
.errors {
  margin: 0;
  padding-left: 18px;
  color: var(--fail);
}
.transitions,
.events {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.transitions li {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 10px;
}
.edge {
  color: var(--text);
}
.reason {
  color: var(--text-muted);
}
.task {
  white-space: pre-wrap;
}
</style>
