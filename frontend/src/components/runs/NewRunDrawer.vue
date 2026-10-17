<script setup lang="ts">
import { computed, nextTick, ref, shallowRef, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import {
  FAULT_KINDS,
  MATCH_MODES,
  MODEL_FAULT_KINDS,
  type AgentConfig,
  type AgentsResponse,
  type RunCreate,
} from '@/api/types'
import ErrorState from '@/components/ui/ErrorState.vue'
import Modal from '@/components/ui/Modal.vue'
import { closeNewRun, newRun, reopenNewRun } from '@/composables/newRun'
import { useJob } from '@/composables/useJob'
import {
  buildExpected,
  buildFaults,
  buildModelFaults,
  emptyExpectedDraft,
  expectedDraft,
  faultDraft,
  modelFaultDraft,
  parseAgentConfig,
  type ExpectedDraft,
  type FaultDraft,
  type FieldIssue,
  type ModelFaultDraft,
} from './runForm'

/*
 * Starts one run: a built-in agent version or a hand-written config, a task, optional tool
 * and model faults, and an optional expected answer. Submits POST /api/runs, polls the job,
 * then opens the trace.
 *
 * A run can take up to its run timeout and cannot be cancelled, so the drawer never locks
 * the app while it executes: closing it keeps polling here (the drawer stays mounted), a
 * notice shows the progress, and it links to the run when it finishes instead of navigating
 * away from whatever the user moved on to. Only a second submission is blocked meanwhile.
 */
const MAX_FAULTS = 10
const router = useRouter()
const job = useJob()
/** The form (newRun.opened) that started the job in flight; null when no job runs. */
const startedFor = ref<number | null>(null)
/** How a run ended while its form was not on screen; shown until dismissed. */
const finished = shallowRef<{ runId: string } | { error: string } | null>(null)

const catalog = shallowRef<AgentsResponse | null>(null)
const catalogError = shallowRef<unknown>(null)

const mode = ref<'version' | 'config'>('version')
const agentRef = ref('')
const configText = ref('')
const task = ref('')
const faults = ref<FaultDraft[]>([])
const modelFaults = ref<ModelFaultDraft[]>([])
const expectOn = ref(false)
const expected = ref<ExpectedDraft>(emptyExpectedDraft())
const issues = ref<FieldIssue[]>([])
const issueList = ref<HTMLElement | null>(null)

const versions = computed(() => catalog.value?.agents.flatMap((a) => a.versions) ?? [])
const refOf = (config: AgentConfig) => `${config.agent}@${config.version}`
const selectedConfig = computed(() => versions.value.find((v) => refOf(v) === agentRef.value) ?? null)
const toolNames = computed(() => catalog.value?.tools.map((t) => t.name) ?? [])
const configIssues = computed(() => (configText.value.trim() ? parseAgentConfig(configText.value).issues : []))

/** Tools the chosen configuration permits: faults on them are the ones that can fire. */
const permittedTools = computed(() => {
  if (mode.value === 'version') return selectedConfig.value?.tools ?? []
  return parseAgentConfig(configText.value).config?.tools ?? []
})

async function loadCatalog(): Promise<void> {
  catalogError.value = null
  try {
    catalog.value = await api.agents()
    applyAgentDefault()
  } catch (e) {
    catalogError.value = e
  }
}

function applyAgentDefault(): void {
  const prefill = newRun.prefill
  if (prefill.agentRef && versions.value.some((v) => refOf(v) === prefill.agentRef)) {
    agentRef.value = prefill.agentRef
    return
  }
  if (versions.value.some((v) => refOf(v) === agentRef.value)) return
  const ofAgent = versions.value.filter((v) => v.agent === prefill.agent)
  const pick = ofAgent[ofAgent.length - 1] ?? versions.value[versions.value.length - 1]
  agentRef.value = pick ? refOf(pick) : ''
}

function reset(): void {
  const prefill = newRun.prefill
  mode.value = 'version'
  agentRef.value = prefill.agentRef ?? ''
  configText.value = ''
  task.value = prefill.task ?? ''
  faults.value = (prefill.faults ?? []).map(faultDraft)
  modelFaults.value = (prefill.modelFaults ?? []).map(modelFaultDraft)
  expectOn.value = Boolean(prefill.expected)
  expected.value = prefill.expected ? expectedDraft(prefill.expected) : emptyExpectedDraft()
  issues.value = []
  job.error.value = null
  if (catalog.value) applyAgentDefault()
  else void loadCatalog()
}

watch(
  () => newRun.opened,
  () => reset(),
)

function useConfigEditor(): void {
  mode.value = 'config'
  if (!configText.value.trim() && selectedConfig.value) {
    configText.value = JSON.stringify(selectedConfig.value, null, 2)
  }
}

function addFault(): void {
  faults.value.push({
    tool: permittedTools.value[0] ?? toolNames.value[0] ?? '',
    kind: 'timeout',
    onCall: '1',
    times: '1',
    payload: '',
  })
}

function addModelFault(): void {
  modelFaults.value.push({ kind: 'invalid_json', onCall: '1', times: '1' })
}

function issueFor(path: string): string | undefined {
  return issues.value.find((issue) => issue.path === path)?.message
}

/** The job in flight was started from the form now on screen. */
const ownJob = computed(() => newRun.open && startedFor.value === newRun.opened)
/** A run still executing while its form is closed (or replaced by a newer one). */
const backgroundRun = computed(() => job.running.value && !ownJob.value)

async function submit(): Promise<void> {
  if (job.running.value) return
  const found: FieldIssue[] = []
  const body: RunCreate = { task: task.value.trim() }
  if (mode.value === 'version') {
    if (!selectedConfig.value) found.push({ path: '/agent_ref', message: 'choose an agent version' })
    body.agent_ref = agentRef.value
  } else {
    const parsed = parseAgentConfig(configText.value)
    found.push(...parsed.issues.map((issue) => ({ path: `/config${issue.path === '/' ? '' : issue.path}`, message: issue.message })))
    body.config = parsed.config
  }
  if (!body.task) found.push({ path: '/task', message: 'is required' })
  else if (body.task.length > 4000) found.push({ path: '/task', message: 'must be at most 4000 characters' })
  body.faults = buildFaults(faults.value, found)
  body.model_faults = buildModelFaults(modelFaults.value, found)
  body.expected = expectOn.value ? buildExpected(expected.value, found) : null
  issues.value = found
  if (found.length) {
    await nextTick()
    issueList.value?.focus()
    return
  }
  const form = newRun.opened
  startedFor.value = form
  finished.value = null
  const done = await job.start(() => api.createRun(body))
  startedFor.value = null
  // The same form still on screen: report there, or open the trace, as before.
  const onScreen = newRun.open && newRun.opened === form
  if (!done) {
    const failure = job.error.value
    if (!failure) return // polling stopped; nothing ended
    const described = describeError(failure)
    if (!onScreen) {
      finished.value = { error: described.message }
      // The notice reports it. A newer form on screen never started this run, so it must not
      // show the failure as its own ("Could not start the run").
      job.error.value = null
    } else if (described.issues.length) {
      // The server's located reasons join the form's own list and mark their fields.
      issues.value = described.issues.map(({ path, message }) => ({ path, message }))
      await nextTick()
      issueList.value?.focus()
    }
    return
  }
  if (!onScreen) {
    finished.value = { runId: done.resource_id }
    return
  }
  closeNewRun()
  await router.push(`/runs/${encodeURIComponent(done.resource_id)}`)
}

const valueLabel = computed(() => {
  if (expected.value.match === 'one_of') {
    return expected.value.valuesJson ? 'Accepted values (one JSON value per line)' : 'Accepted values (one per line)'
  }
  return expected.value.match === 'exact' ? 'Value (JSON)' : 'Value'
})

const submitError = computed(() => (job.error.value ? describeError(job.error.value).message : null))
const progress = computed(() => {
  const state = job.job.value?.state
  return state === 'running' ? 'Running the agent…' : 'Queued…'
})
</script>

<template>
  <!-- Closing never waits for the run: it keeps executing and the notice below follows it. -->
  <Modal :open="newRun.open" title="New run" variant="drawer" @close="closeNewRun">
    <ErrorState v-if="catalogError" :error="catalogError" title="Could not load the agents" compact @retry="loadCatalog" />
    <form v-else id="new-run-form" class="form" novalidate @submit.prevent="submit">
      <p v-if="newRun.prefill.source" class="callout info source">Prefilled from {{ newRun.prefill.source }}</p>

      <fieldset class="group">
        <legend class="section-title">Agent</legend>
        <div class="modes" role="radiogroup" aria-label="How to choose the agent">
          <label><input v-model="mode" type="radio" name="agent-mode" value="version" /> Built-in version</label>
          <label>
            <input type="radio" name="agent-mode" value="config" :checked="mode === 'config'" @change="useConfigEditor" />
            Full config (JSON)
          </label>
        </div>
        <label v-if="mode === 'version'" class="field">
          <span>Agent version</span>
          <select
            v-model="agentRef"
            class="select mono-select"
            data-autofocus
            :aria-invalid="issueFor('/agent_ref') ? 'true' : undefined"
          >
            <optgroup v-for="agent in catalog?.agents ?? []" :key="agent.name" :label="agent.title">
              <option v-for="v in agent.versions" :key="refOf(v)" :value="refOf(v)">
                {{ refOf(v) }}{{ v.label ? ` · ${v.label}` : '' }}
              </option>
            </optgroup>
          </select>
        </label>
        <template v-else>
          <label class="field">
            <span>AgentConfig JSON</span>
            <textarea
              v-model="configText"
              class="textarea config"
              rows="14"
              spellcheck="false"
              :aria-invalid="configIssues.length ? 'true' : undefined"
              aria-describedby="config-issues"
            ></textarea>
          </label>
          <ul v-if="configIssues.length" id="config-issues" class="issues" aria-live="polite">
            <li v-for="issue in configIssues" :key="issue.path + issue.message">
              <code>{{ issue.path }}</code> {{ issue.message }}
            </li>
          </ul>
          <p v-else-if="configText.trim()" id="config-issues" class="valid" aria-live="polite">Valid configuration.</p>
        </template>
      </fieldset>

      <label class="field">
        <span>Task</span>
        <textarea
          v-model="task"
          class="textarea task"
          rows="3"
          maxlength="4000"
          :aria-invalid="issueFor('/task') ? 'true' : undefined"
        ></textarea>
      </label>

      <fieldset class="group">
        <legend class="section-title">Tool faults</legend>
        <p class="hint">Blank "on call" means every call; blank "times" means every later call.</p>
        <div v-for="(fault, i) in faults" :key="i" class="row fault-row">
          <label class="field">
            <span>Tool</span>
            <select v-model="fault.tool" class="select mono-select" :aria-invalid="issueFor(`/faults/${i}/tool`) ? 'true' : undefined">
              <option v-for="name in toolNames" :key="name" :value="name">
                {{ name }}{{ permittedTools.includes(name) ? '' : ' (not permitted)' }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>Kind</span>
            <select v-model="fault.kind" class="select">
              <option v-for="kind in FAULT_KINDS" :key="kind" :value="kind">{{ kind.replace('_', ' ') }}</option>
            </select>
          </label>
          <label class="field narrow">
            <span>On call</span>
            <input v-model="fault.onCall" class="input" inputmode="numeric" :aria-invalid="issueFor(`/faults/${i}/on_call`) ? 'true' : undefined" />
          </label>
          <label class="field narrow">
            <span>Times</span>
            <input v-model="fault.times" class="input" inputmode="numeric" :aria-invalid="issueFor(`/faults/${i}/times`) ? 'true' : undefined" />
          </label>
          <label class="field wide">
            <span>Payload (optional)</span>
            <input v-model="fault.payload" class="input" maxlength="8000" />
          </label>
          <button type="button" class="btn ghost remove" :aria-label="`Remove tool fault ${i + 1}`" @click="faults.splice(i, 1)">
            Remove
          </button>
        </div>
        <button type="button" class="btn add" :disabled="faults.length >= MAX_FAULTS || !toolNames.length" @click="addFault">
          Add tool fault
        </button>
      </fieldset>

      <fieldset class="group">
        <legend class="section-title">Model faults</legend>
        <div v-for="(fault, i) in modelFaults" :key="i" class="row">
          <label class="field">
            <span>Kind</span>
            <select v-model="fault.kind" class="select">
              <option v-for="kind in MODEL_FAULT_KINDS" :key="kind" :value="kind">{{ kind.replace('_', ' ') }}</option>
            </select>
          </label>
          <label class="field narrow">
            <span>On response</span>
            <input v-model="fault.onCall" class="input" inputmode="numeric" :aria-invalid="issueFor(`/model_faults/${i}/on_call`) ? 'true' : undefined" />
          </label>
          <label class="field narrow">
            <span>Times</span>
            <input v-model="fault.times" class="input" inputmode="numeric" :aria-invalid="issueFor(`/model_faults/${i}/times`) ? 'true' : undefined" />
          </label>
          <button type="button" class="btn ghost remove" :aria-label="`Remove model fault ${i + 1}`" @click="modelFaults.splice(i, 1)">
            Remove
          </button>
        </div>
        <button type="button" class="btn add" :disabled="modelFaults.length >= MAX_FAULTS" @click="addModelFault">
          Add model fault
        </button>
      </fieldset>

      <fieldset class="group">
        <legend class="section-title">Expected answer</legend>
        <label class="check"><input v-model="expectOn" type="checkbox" /> Evaluate the run against an expected answer</label>
        <div v-if="expectOn" class="row">
          <label class="field">
            <span>Output field</span>
            <input v-model="expected.field" class="input mono-input" :aria-invalid="issueFor('/expected/field') ? 'true' : undefined" />
          </label>
          <label class="field">
            <span>Match</span>
            <select v-model="expected.match" class="select">
              <option v-for="m in MATCH_MODES" :key="m" :value="m">{{ m.replace('_', ' ') }}</option>
            </select>
          </label>
          <label v-if="expected.match === 'numeric'" class="field narrow">
            <span>Tolerance</span>
            <input v-model="expected.tolerance" class="input" inputmode="decimal" :aria-invalid="issueFor('/expected/tolerance') ? 'true' : undefined" />
          </label>
          <label v-if="expected.match === 'contains'" class="field">
            <span>Max characters (optional)</span>
            <input v-model="expected.maxChars" class="input" inputmode="numeric" :aria-invalid="issueFor('/expected/max_chars') ? 'true' : undefined" />
          </label>
          <label class="field full">
            <span>{{ valueLabel }}</span>
            <textarea
              v-model="expected.value"
              class="textarea"
              :rows="expected.match === 'one_of' ? 3 : 1"
              :aria-invalid="issueFor('/expected/value') || issueFor('/expected/values') ? 'true' : undefined"
            ></textarea>
          </label>
        </div>
        <p v-if="newRun.prefill.source" class="hint">
          An ad-hoc run checks the expected answer only. The case's tool expectations, permitted tools and limit
          overrides apply when the dataset is evaluated.
        </p>
      </fieldset>

      <div v-if="issues.length" ref="issueList" class="callout fail" role="alert" tabindex="-1">
        <div>
          <strong>Fix these before starting the run:</strong>
          <ul class="issues">
            <li v-for="issue in issues" :key="issue.path + issue.message"><code>{{ issue.path }}</code> {{ issue.message }}</li>
          </ul>
        </div>
      </div>
      <p v-if="submitError" class="callout fail" role="alert">Could not start the run: {{ submitError }}</p>
      <p v-if="job.running.value && ownJob" class="progress" role="status">
        {{ progress }}
        <span class="hint">You can close this drawer: the run keeps going, and a notice links to it when it finishes.</span>
      </p>
      <p v-else-if="job.running.value" class="progress" role="status">
        Another run is still in progress. Start this one when it finishes.
      </p>
    </form>
    <template #footer>
      <button type="button" class="btn" @click="closeNewRun">{{ job.running.value ? 'Close' : 'Cancel' }}</button>
      <button type="submit" form="new-run-form" class="btn primary" :disabled="job.running.value || !catalog">
        {{ job.running.value ? 'Running…' : 'Start run' }}
      </button>
    </template>
  </Modal>
  <div class="run-notice-region" aria-live="polite">
    <div v-if="backgroundRun" class="run-notice panel">
      <span>Run in progress: {{ progress }}</span>
      <button type="button" class="btn ghost" @click="reopenNewRun">Show</button>
    </div>
    <div v-else-if="finished" class="run-notice panel" :class="{ failed: 'error' in finished }">
      <template v-if="'runId' in finished">
        <span>The run finished.</span>
        <RouterLink :to="`/runs/${encodeURIComponent(finished.runId)}`" class="btn" @click="finished = null">
          Open the trace
        </RouterLink>
      </template>
      <span v-else>The run failed: {{ finished.error }}</span>
      <button type="button" class="btn ghost" @click="finished = null">Dismiss</button>
    </div>
  </div>
</template>

<style scoped>
.form {
  display: grid;
  gap: 16px;
}
.group {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
legend {
  margin-bottom: 6px;
  padding: 0;
}
.modes {
  display: flex;
  gap: 16px;
  font-size: 13px;
}
.modes label,
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-0);
}
.field.narrow {
  width: 84px;
}
.field.wide {
  flex: 1 1 180px;
}
.field.full {
  flex: 1 1 100%;
}
.remove {
  margin-left: auto;
}
.add {
  justify-self: start;
}
.mono-select,
.mono-input {
  font-family: var(--font-mono);
  font-size: 12px;
}
.task {
  font-family: var(--font-ui);
  font-size: 13px;
}
.hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
}
.issues {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
}
.issues code {
  color: var(--text);
}
.valid {
  margin: 0;
  font-size: 12.5px;
  color: var(--ok);
}
.progress {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}
.progress .hint {
  display: block;
}
.source {
  font-size: 12.5px;
}
/* Below the modal layer: a run's progress never covers a dialog. */
.run-notice-region {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 25;
  max-width: min(440px, calc(100vw - 32px));
}
.run-notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  padding: 10px 12px;
  border-left: 3px solid var(--accent);
  box-shadow: 0 8px 24px var(--shadow);
  font-size: 13px;
}
.run-notice.failed {
  border-left-color: var(--fail);
}
.run-notice > span {
  flex: 1 1 200px;
}
</style>
