import { describe, expect, it } from 'vitest'
import { ablation, ablationVariant } from '@/__tests__/builders'
import type { AblationPair, ConfigDifference } from '@/api/types'
import {
  ablatableDimensions,
  ablationPlan,
  effectDomain,
  formatEffect,
  interactions,
  overallReading,
  pairKey,
  variantName,
} from '../attribution'

const diff = (path: string, dimension: ConfigDifference['dimension']): ConfigDifference => ({ path, dimension, a: 1, b: 2 })

function pair(dimensions: AblationPair['dimensions'], effect: number, interaction: number): AblationPair {
  return {
    dimensions,
    agent_ref: 'x',
    config_hash: 'h',
    suite_id: `s-${dimensions.join('-')}`,
    passed: 0,
    total: 10,
    harness_errors: 0,
    effect_pp: effect,
    interaction_pp: interaction,
    gained: [],
    lost: [],
  }
}

describe('ablatableDimensions', () => {
  it('keeps dimensions a top-level configuration field expresses, once each, in diff order', () => {
    expect(
      ablatableDimensions([
        diff('retry.max_tool_retries', 'retry'),
        diff('retry.retry_on', 'retry'),
        diff('prompt_version', 'prompt'),
        diff('isolate_untrusted', 'isolation'),
        diff('model.model', 'model'),
      ]),
    ).toEqual(['prompt', 'model', 'retry', 'isolation'])
  })

  it('leaves out resolved content, the environment and other differences', () => {
    expect(
      ablatableDimensions([
        diff('resolved.prompt', 'prompt'),
        diff('resolved.tools.search_documents', 'tool_schema'),
        diff('environment.agentscope_version', 'other'),
        diff('effective_config_hash', 'other'),
      ]),
    ).toEqual([])
  })
})

describe('ablationPlan', () => {
  it.each([
    [1, { pairs: 0, pairsSkipped: false }],
    [2, { pairs: 1, pairsSkipped: false }],
    [5, { pairs: 10, pairsSkipped: false }],
    [6, { pairs: 15, pairsSkipped: false }],
    [7, { pairs: 0, pairsSkipped: true }],
  ])('%i dimensions: k choose 2 pairs up to the pairwise limit', (n, plan) => {
    expect(ablationPlan(n)).toEqual(plan)
  })
})

describe('overallReading', () => {
  const read = (total: number, sum: number) =>
    overallReading(ablation({ total_effect_pp: total, sum_of_solo_effects_pp: sum, interaction_pp: total - sum }))

  it('says when most of the change only appears in combination', () => {
    // research_assistant v1 -> v2: +55.2 pp in total, +20.7 pp from the dimensions alone.
    expect(read(55.2, 20.7)).toBe('Most of the change only appears when dimensions are combined.')
    expect(read(40, 30)).toBe('Part of the change only appears when dimensions are combined.')
    expect(read(-30, -5)).toBe('Most of the change only appears when dimensions are combined.')
  })

  it('says when the solo effects account for the change, overlap, or there is none', () => {
    expect(read(20, 20.02)).toBe('The dimensions add up: their effects one at a time account for the whole change.')
    expect(read(20, 35)).toBe('The dimensions overlap or cancel out: one at a time they add up to more than the change together.')
    expect(read(0, 10)).toBe('The dimensions overlap or cancel out: one at a time they add up to more than the change together.')
    expect(read(0, 0)).toBe('No dimension changes the pass rate, alone or all together.')
  })
})

describe('interactions', () => {
  it('lists only pairs with an interaction, largest first, keeping ties in pair order', () => {
    const rows = interactions(ablation())
    expect(rows.map((r) => [r.pair.dimensions.join('+'), r.pair.interaction_pp, r.reading])).toEqual([
      ['prompt+isolation', 30, 'only together'],
      ['graph+retry', 10, 'better together than apart'],
      ['isolation+retry', -10, 'worse together than apart'],
    ])
    expect(rows[0]!.solo).toEqual([0, 0])
  })

  it('treats an interaction that prints as ±0 pp as none', () => {
    const data = ablation({ pairs: [pair(['prompt', 'graph'], 10.03, 0.04)] })
    expect(interactions(data)).toEqual([])
  })

  it('reads two dimensions that each do the whole job as overlapping', () => {
    const data = ablation({
      variants: [ablationVariant('prompt', 'forward', 7, 20), ablationVariant('graph', 'forward', 7, 20)],
      pairs: [pair(['prompt', 'graph'], 20, -20)],
    })
    expect(interactions(data)[0]!.reading).toBe('overlap: either one is enough')
  })

  it('reads the sign alone when a solo effect is not reported', () => {
    const data = ablation({ variants: [], pairs: [pair(['prompt', 'graph'], 20, 5)] })
    expect(interactions(data)[0]).toMatchObject({ solo: [null, null], reading: 'better together than apart' })
  })
})

describe('formatEffect', () => {
  it('always prints one decimal, so effects line up', () => {
    expect([31.034, 10.3448, 0, -0.04, -3.46].map(formatEffect)).toEqual(['+31.0 pp', '+10.3 pp', '±0.0 pp', '±0.0 pp', '−3.5 pp'])
  })
})

describe('naming', () => {
  it('names variants in words', () => {
    expect(variantName('forward', ['prompt'])).toBe('A + prompt')
    expect(variantName('forward', ['prompt', 'isolation'])).toBe('A + prompt + untrusted-output isolation')
    expect(variantName('backward', ['retry'])).toBe('B − retry policy')
  })

  it('keys pair runs in configuration order, whatever order the pair is given in', () => {
    expect(pairKey(ablation(), ['isolation', 'prompt'])).toBe('pair:prompt+isolation')
    expect(pairKey(ablation(), ['prompt', 'isolation'])).toBe('pair:prompt+isolation')
  })
})

describe('effectDomain', () => {
  it.each([
    [[0, 0], 10],
    [[3.4, -10], 10],
    [[10.3, -2], 20],
    [[55.2, 31], 60],
    [[-48, 12], 50],
    [[-79.96], 80],
    [[100], 100],
  ])('%j fits in ±%i pp', (values, domain) => {
    expect(effectDomain(values)).toBe(domain)
  })
})
