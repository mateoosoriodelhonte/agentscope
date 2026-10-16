import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { GateRule } from '@/api/types'
import GateChecklist from '../GateChecklist.vue'
import { formatActual, formatThreshold, ruleLabel } from '../gate'

const rule = (overrides: Partial<GateRule>): GateRule => ({
  name: 'min_pass_rate',
  threshold: 0.8,
  actual: '7/10',
  passed: false,
  detail: '',
  ...overrides,
})

describe('GateChecklist', () => {
  it('has one row per rule with threshold, actual and an icon-and-text result', () => {
    const wrapper = mount(GateChecklist, {
      props: {
        gate: {
          passed: false,
          baseline_used: true,
          rules: [
            rule({}),
            rule({ name: 'max_new_regressions', threshold: 0, actual: 2, detail: 'regressed: qa-3, qa-7' }),
            rule({ name: 'max_harness_errors', threshold: 0, actual: 0, passed: true }),
          ],
        },
      },
    })
    expect(wrapper.find('.section-head').text()).toContain('gate failed')
    expect(wrapper.text()).toContain('compared with a baseline')
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows.map((r) => r.findAll('td').map((td) => td.text()))).toEqual([
      ['≥ 80% of cases', '7/10', 'failed'],
      ['≤ 0', '2', 'failed'],
      ['≤ 0', '0', 'passed'],
    ])
    expect(rows[1]!.find('th').text()).toContain('regressed: qa-3, qa-7')
    expect(rows[0]!.classes()).toContain('failed')
    expect(rows[2]!.find('.badge').classes()).toContain('ok')
    expect(rows[2]!.find('.badge svg').exists()).toBe(true)
  })

  it('mentions a missing baseline only when a rule needs one', () => {
    const without = (rules: GateRule[]) =>
      mount(GateChecklist, { props: { gate: { passed: false, baseline_used: false, rules } } }).find('.section-head').text()
    expect(without([rule({})])).not.toContain('baseline')
    expect(without([rule({}), rule({ name: 'max_new_regressions', threshold: 0, actual: null })])).toContain(
      'no baseline: regression rules cannot be measured',
    )
  })

  it('says when no gate was configured', () => {
    const wrapper = mount(GateChecklist, { props: { gate: null } })
    expect(wrapper.text()).toContain('no gate')
    expect(wrapper.text()).toContain('No gate thresholds were configured')
    expect(wrapper.find('table').exists()).toBe(false)
  })
})

describe('gate rule formatting', () => {
  it('formats required checks, baseline rules and unmeasured values', () => {
    const required = rule({
      name: 'required_check:policy_compliance',
      threshold: { max_failed: 0, min_applicable: 1 },
      actual: { failed: 1, applicable: 4 },
    })
    expect(ruleLabel(required)).toBe('Required check policy_compliance')
    expect(formatThreshold(required)).toBe('0 failures, applies to ≥ 1 case')
    expect(formatActual(required)).toBe('1 failed of 4 applicable')
    const drop = rule({ name: 'max_pass_rate_drop_pp', threshold: 5, actual: null, detail: 'requires a baseline' })
    expect(formatThreshold(drop)).toBe('≤ 5 pp')
    expect(formatActual(drop)).toBe('not measured')
    expect(ruleLabel(rule({ name: 'custom_rule' }))).toBe('custom_rule')
  })
})
