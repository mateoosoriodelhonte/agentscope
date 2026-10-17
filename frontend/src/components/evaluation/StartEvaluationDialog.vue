<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { api } from '@/api/client'
import { describeError } from '@/api/errors'
import {
  CHECK_NAMES,
  type AgentsResponse,
  type CheckName,
  type DatasetSummary,
  type GateThresholds,
  type JobAccepted,
  type ValidationIssue,
} from '@/api/types'
import ErrorState from '@/components/ui/ErrorState.vue'
import IssueList from '@/components/ui/IssueList.vue'
import Modal from '@/components/ui/Modal.vue'
import type { FieldIssue } from '@/composables/parseJson'

/*
 * Starts an evaluation: one agent version over one dataset version, with optional gate
 * thresholds. The API answers 202 with a job; the parent follows it.
 */
const props = defineProps<{ open: boolean; dataset: string | null }>()
const emit = defineEmits<{ close: []; started: [job: JobAccepted] }>()

const datasets = shallowRef<DatasetSummary[] | null>(null)
const catalog = shallowRef<AgentsResponse | null>(null)
const loadError = shallowRef<unknown>(null)

const datasetRef = ref('')
const agentRef = ref('')
const label = ref('')
const gateOn = ref(false)
const minPassRate = ref('')
const maxInvalid = ref('')
const maxPolicy = ref('')
const maxUnauthorized = ref('0')
const maxHarness = ref('0')
const required = ref<CheckName[]>([])
const issues = ref<FieldIssue[]>([])
const busy = ref(false)
const error = ref<string | null>(null)
/** The server's reasons for refusing the evaluation (e.g. cases that do not fit the agent). */
const errorIssues = ref<ValidationIssue[]>([])

const refOf = (d: DatasetSummary) => `${d.name}@${d.version}`
const versions = computed(() => catalog.value?.agents.flatMap((a) => a.versions.map((v) => `${v.agent}@${v.version}`)) ?? [])

async function load(): Promise<void> {
  loadError.value = null
  try {
    ;[datasets.value, catalog.value] = await Promise.all([api.listDatasets(), api.agents()])
    applyDefaults()
  } catch (e) {
    loadError.value = e
  }
}

function applyDefaults(): void {
  const list = datasets.value ?? []
  const chosen = list.find((d) => refOf(d) === props.dataset) ?? list.find((d) => refOf(d) === datasetRef.value) ?? list[0]
  datasetRef.value = chosen ? refOf(chosen) : ''
  if (!versions.value.includes(agentRef.value)) {
    const forAgent = versions.value.filter((v) => chosen?.agent && v.startsWith(`${chosen.agent}@`))
    agentRef.value = forAgent[forAgent.length - 1] ?? versions.value[versions.value.length - 1] ?? ''
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    issues.value = []
    error.value = null
    errorIssues.value = []
    if (datasets.value && catalog.value) applyDefaults()
    else void load()
  },
  { immediate: true },
)

function count(raw: string, name: keyof GateThresholds, found: FieldIssue[]): number | null {
  const text = raw.trim()
  if (text === '') return null
  const value = Number(text)
  if (!Number.isInteger(value) || value < 0) {
    found.push({ path: `/thresholds/${name}`, message: 'must be a whole number of at least 0' })
    return null
  }
  return value
}

function thresholds(found: FieldIssue[]): GateThresholds | null {
  if (!gateOn.value) return null
  let rate: number | null = null
  if (minPassRate.value.trim() !== '') {
    const percent = Number(minPassRate.value.trim())
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
      found.push({ path: '/thresholds/min_pass_rate', message: 'must be a percentage from 0 to 100' })
    } else {
      // The gate reads the threshold through str() as an exact decimal, so send the decimal
      // that was typed: 1.1 / 100 is 0.011000000000000001 in floating point.
      rate = Number((percent / 100).toPrecision(15))
    }
  }
  return {
    min_pass_rate: rate,
    max_invalid_outputs: count(maxInvalid.value, 'max_invalid_outputs', found),
    max_policy_violation_cases: count(maxPolicy.value, 'max_policy_violation_cases', found),
    max_unauthorized_tool_executions: count(maxUnauthorized.value, 'max_unauthorized_tool_executions', found),
    max_harness_errors: count(maxHarness.value, 'max_harness_errors', found),
    required_checks: [...required.value],
  }
}

async function submit(): Promise<void> {
  if (busy.value) return
  const found: FieldIssue[] = []
  const at = datasetRef.value.lastIndexOf('@')
  if (at <= 0) found.push({ path: '/dataset', message: 'choose a dataset version' })
  if (!agentRef.value) found.push({ path: '/agent_ref', message: 'choose an agent version' })
  const gate = thresholds(found)
  issues.value = found
  if (found.length) return
  busy.value = true
  error.value = null
  errorIssues.value = []
  try {
    const accepted = await api.createEvaluation({
      agent_ref: agentRef.value,
      dataset: { name: datasetRef.value.slice(0, at), version: datasetRef.value.slice(at + 1) },
      label: label.value.trim(),
      thresholds: gate,
    })
    emit('started', accepted)
  } catch (e) {
    const described = describeError(e)
    error.value = described.message
    errorIssues.value = described.issues
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Modal :open="open" title="Start an evaluation" :busy="busy" @close="emit('close')">
    <ErrorState v-if="loadError" :error="loadError" title="Could not load datasets and agents" compact @retry="load" />
    <form v-else id="start-evaluation" class="form" novalidate @submit.prevent="submit">
      <p v-if="datasets && !datasets.length" class="callout warn">Import a dataset first: there is nothing to evaluate.</p>
      <label class="field">
        <span>Dataset version</span>
        <select v-model="datasetRef" class="select mono" data-autofocus>
          <option v-for="d in datasets ?? []" :key="refOf(d)" :value="refOf(d)">{{ refOf(d) }} · {{ d.case_count }} cases</option>
        </select>
      </label>
      <label class="field">
        <span>Agent version</span>
        <select v-model="agentRef" class="select mono">
          <option v-for="v in versions" :key="v" :value="v">{{ v }}</option>
        </select>
      </label>
      <label class="field">
        <span>Label (optional)</span>
        <input v-model="label" class="input" maxlength="200" />
      </label>
      <fieldset class="gate">
        <legend><label class="check"><input v-model="gateOn" type="checkbox" /> Apply a gate</label></legend>
        <div v-if="gateOn" class="grid">
          <label class="field">
            <span>Minimum pass rate (%)</span>
            <input v-model="minPassRate" class="input" inputmode="decimal" :aria-invalid="issues.some((i) => i.path === '/thresholds/min_pass_rate') ? 'true' : undefined" />
          </label>
          <label class="field">
            <span>Max invalid outputs</span>
            <input v-model="maxInvalid" class="input" inputmode="numeric" />
          </label>
          <label class="field">
            <span>Max cases with policy violations</span>
            <input v-model="maxPolicy" class="input" inputmode="numeric" />
          </label>
          <label class="field">
            <span>Max unauthorized tool executions</span>
            <input v-model="maxUnauthorized" class="input" inputmode="numeric" />
          </label>
          <label class="field">
            <span>Max harness errors</span>
            <input v-model="maxHarness" class="input" inputmode="numeric" />
          </label>
          <fieldset class="required">
            <legend class="field-legend">Required checks (zero failures, applies to at least one case)</legend>
            <label v-for="name in CHECK_NAMES" :key="name" class="check mono">
              <input v-model="required" type="checkbox" :value="name" /> {{ name }}
            </label>
          </fieldset>
        </div>
      </fieldset>
      <ul v-if="issues.length" class="callout fail issues" role="alert">
        <li v-for="issue in issues" :key="issue.path"><code>{{ issue.path }}</code> {{ issue.message }}</li>
      </ul>
      <div v-if="error" class="callout fail" role="alert">
        <div>
          Could not start the evaluation: {{ error }}
          <IssueList v-if="errorIssues.length" :issues="errorIssues" />
        </div>
      </div>
    </form>
    <template #footer>
      <button type="button" class="btn" :disabled="busy" @click="emit('close')">Cancel</button>
      <button type="submit" form="start-evaluation" class="btn primary" :disabled="busy || !datasets?.length">
        {{ busy ? 'Starting…' : 'Start evaluation' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.form {
  display: grid;
  gap: 12px;
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.gate,
.required {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 12px;
  margin-top: 8px;
}
.required {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 12px;
}
.field-legend {
  margin-bottom: 4px;
  font-size: 11.5px;
  color: var(--text-muted);
}
.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.issues {
  display: block;
  padding-left: 28px;
  font-size: 12.5px;
}
</style>
