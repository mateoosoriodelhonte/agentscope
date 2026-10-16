<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { CHECK_NAMES, type CaseResult, type CheckName, type CheckStats, type CheckStatus } from '@/api/types'
import Icon from '@/components/ui/Icon.vue'
import type { IconName } from '@/components/ui/icons'
import { ratio } from '@/composables/format'

/*
 * Cases × the ten checks. Each state has its own glyph shape (passed ✓, failed ✕, not
 * applicable –), so the matrix reads without colour; a check that did not run leaves the
 * cell empty. Every cell links to the case's trace with ?check= so the evidence opens. The
 * grid has one tab stop; arrow keys, Home and End move within it.
 */
const props = defineProps<{
  suiteId: string
  cases: CaseResult[]
  stats: Partial<Record<CheckName, CheckStats>>
}>()

interface GlyphSpec {
  icon: IconName
  label: string
}

const GLYPHS: Record<CheckStatus, GlyphSpec> = {
  passed: { icon: 'check', label: 'passed' },
  failed: { icon: 'x', label: 'failed' },
  not_applicable: { icon: 'minus', label: 'not applicable' },
}

const SHORT: Record<CheckName, string> = {
  answer_correctness: 'answer',
  schema_validity: 'schema',
  required_tool_calls: 'required',
  forbidden_tool_calls: 'forbidden',
  tool_order: 'order',
  tool_arguments: 'args',
  task_completion: 'complete',
  timeout_behavior: 'timeout',
  error_recovery: 'recovery',
  policy_compliance: 'policy',
}

const rows = computed(() =>
  props.cases.map((c) => ({
    result: c,
    cells: CHECK_NAMES.map((name) => ({ name, status: c.checks.find((check) => check.name === name)?.status ?? null })),
  })),
)

const caseLink = (c: CaseResult, check?: CheckName) => ({
  path: `/evaluations/${encodeURIComponent(props.suiteId)}/cases/${encodeURIComponent(c.case_id)}`,
  query: check ? { check } : {},
})

const cursor = ref({ row: 0, col: 0 })
const grid = ref<HTMLElement | null>(null)
const COLS = CHECK_NAMES.length + 1

function cellAt(row: number, col: number): HTMLElement | null {
  return grid.value?.querySelector<HTMLElement>(`[data-cell="${row}:${col}"]`) ?? null
}

/** Moves along the row or column, skipping cells with no link (checks that did not run). */
function move(dRow: number, dCol: number, to?: { row: number; col: number }): void {
  let { row, col } = to ?? cursor.value
  if (!to) {
    do {
      row += dRow
      col += dCol
    } while (row >= 0 && row < rows.value.length && col >= 0 && col < COLS && !cellAt(row, col))
  }
  if (row < 0 || row >= rows.value.length || col < 0 || col >= COLS || !cellAt(row, col)) return
  cursor.value = { row, col }
  void nextTick(() => cellAt(row, col)?.focus())
}

function onKeydown(event: KeyboardEvent): void {
  const moves: Record<string, () => void> = {
    ArrowRight: () => move(0, 1),
    ArrowLeft: () => move(0, -1),
    ArrowDown: () => move(1, 0),
    ArrowUp: () => move(-1, 0),
    Home: () => move(0, 0, { row: cursor.value.row, col: 0 }),
    End: () => {
      for (let col = COLS - 1; col >= 0; col--) {
        if (cellAt(cursor.value.row, col)) return move(0, 0, { row: cursor.value.row, col })
      }
    },
  }
  const action = moves[event.key]
  if (!action) return
  event.preventDefault()
  action()
}

const tabIndex = (row: number, col: number) => (cursor.value.row === row && cursor.value.col === col ? 0 : -1)
</script>

<template>
  <div class="matrix-wrap">
    <ul class="legend" aria-label="Legend">
      <li v-for="(glyph, status) in GLYPHS" :key="status" :class="status">
        <span class="glyph"><Icon :name="glyph.icon" :size="12" /></span>{{ glyph.label }}
      </li>
      <li class="not-run"><span class="glyph"></span>check did not run</li>
    </ul>
    <div class="table-wrap panel">
      <table ref="grid" class="matrix" role="grid" aria-label="Cases by check" @keydown="onKeydown">
        <thead>
          <tr>
            <th scope="col" class="case-col">Case</th>
            <th v-for="name in CHECK_NAMES" :key="name" scope="col" class="check-col">
              <abbr :title="name">{{ SHORT[name] }}</abbr>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in rows" :key="row.result.case_id" :class="{ failing: !row.result.passed }">
            <th scope="row" class="case-col" role="rowheader">
              <RouterLink
                :to="caseLink(row.result)"
                class="case-link mono"
                :data-cell="`${r}:0`"
                :tabindex="tabIndex(r, 0)"
                @focus="cursor = { row: r, col: 0 }"
              >
                <Icon :name="row.result.passed ? 'check' : 'x'" :size="12" class="verdict" />
                <span class="visually-hidden">{{ row.result.passed ? 'passed:' : 'failed:' }}</span>
                {{ row.result.case_id }}
              </RouterLink>
              <span v-if="row.result.harness_error" class="harness">harness error</span>
            </th>
            <td v-for="(cell, c) in row.cells" :key="cell.name" role="gridcell" class="cell" :class="cell.status ?? 'not-run'">
              <RouterLink
                v-if="cell.status"
                :to="caseLink(row.result, cell.name)"
                class="glyph-link"
                :data-cell="`${r}:${c + 1}`"
                :data-status="cell.status"
                :tabindex="tabIndex(r, c + 1)"
                :aria-label="`${row.result.case_id}, ${cell.name}: ${GLYPHS[cell.status].label}`"
                :title="`${cell.name}: ${GLYPHS[cell.status].label}`"
                @focus="cursor = { row: r, col: c + 1 }"
              >
                <Icon :name="GLYPHS[cell.status].icon" :size="13" />
              </RouterLink>
              <span v-else class="visually-hidden">{{ cell.name }} did not run</span>
            </td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" class="case-col">Passed / applicable</th>
            <td v-for="name in CHECK_NAMES" :key="name" class="stat mono">
              <template v-if="stats[name] && stats[name]!.passed + stats[name]!.failed > 0">
                {{ ratio(stats[name]!.passed, stats[name]!.passed + stats[name]!.failed) }}
              </template>
              <span v-else class="faint" title="Applied to no case">n/a</span>
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.matrix-wrap {
  display: grid;
  gap: 6px;
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12px;
  color: var(--text-muted);
}
.legend li {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.matrix {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.matrix th,
.matrix td {
  border-bottom: 1px solid var(--border);
}
thead th {
  position: sticky;
  top: 0;
  padding: 6px 4px;
  background: var(--surface-2);
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
}
abbr {
  text-decoration: none;
  cursor: help;
}
.case-col {
  min-width: 200px;
  max-width: 320px;
  padding: 4px 10px;
  text-align: left;
  font-weight: 400;
}
.check-col {
  width: 64px;
  text-align: center;
}
tbody tr:last-child > * {
  border-bottom: 0;
}
tbody tr.failing .case-col {
  box-shadow: inset 3px 0 0 var(--fail);
}
.case-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text);
}
.case-link:hover {
  color: var(--accent);
}
.verdict {
  color: var(--text-muted);
}
.failing .verdict {
  color: var(--fail);
}
.harness {
  margin-left: 6px;
  font-size: 11px;
  color: var(--fail);
}
.cell {
  padding: 2px;
  text-align: center;
}
.glyph-link,
.glyph {
  display: inline-grid;
  place-items: center;
  width: 26px;
  height: 22px;
  border-radius: var(--radius-sm);
}
.glyph {
  width: 18px;
  height: 18px;
  border: 1px solid var(--border);
}
.glyph-link:hover {
  outline: 1px solid var(--border-strong);
}
.passed .glyph-link,
.passed .glyph {
  color: var(--ok);
}
.failed .glyph-link,
.failed .glyph {
  background: var(--fail-soft);
  color: var(--fail);
}
.not_applicable .glyph-link,
.not_applicable .glyph {
  color: var(--na);
}
.not-run .glyph {
  border-style: dashed;
}
tfoot th,
tfoot td {
  border-top: 1px solid var(--border-strong);
  border-bottom: 0;
  padding: 5px 4px;
  font-size: 11px;
  color: var(--text-muted);
}
.stat {
  text-align: center;
}
</style>
