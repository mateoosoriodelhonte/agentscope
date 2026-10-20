import type { Ablation, AblationDirection, AblationPair, AblationVariant, ConfigDifference, ConfigDimension } from '@/api/types'
import { MAX_PAIRWISE_DIMENSIONS } from '@/api/types'
import { formatSigned } from '@/composables/format'
import { DIMENSION_LABELS, DIMENSION_ORDER } from './dimensions'

/*
 * What the attribution panel derives from a comparison and an ablation: which differences a
 * variant can isolate, what an ablation will run, and plain-language readings of its effects.
 * Effects are percentage points over the same n cases, compared at the 0.1 pp they are
 * printed with, so a value that reads "±0 pp" is never described as an effect.
 */

/** Top-level AgentConfig fields that carry a behaviour dimension (experiments/compare.py). */
const CONFIG_FIELDS = new Set([
  'prompt_version',
  'model',
  'tools',
  'tool_schema_version',
  'graph',
  'isolate_untrusted',
  'retry',
  'limits',
])

/**
 * The dimensions a configuration field expresses, so a variant can change them alone.
 * Resolved content (`resolved.*`), the environment and `other` differences cannot be.
 */
export function ablatableDimensions(diff: ConfigDifference[]): ConfigDimension[] {
  const found = new Set(
    diff.filter((d) => d.dimension !== 'other' && CONFIG_FIELDS.has(d.path.split('.')[0]!)).map((d) => d.dimension),
  )
  return DIMENSION_ORDER.filter((d) => found.has(d))
}

/**
 * The pairs an ablation of `dimensions` evaluates besides its 2 × dimensions variants:
 * k choose 2, or none above MAX_PAIRWISE_DIMENSIONS.
 */
export function ablationPlan(dimensions: number): { pairs: number; pairsSkipped: boolean } {
  const pairsSkipped = dimensions > MAX_PAIRWISE_DIMENSIONS
  return { pairs: pairsSkipped ? 0 : (dimensions * (dimensions - 1)) / 2, pairsSkipped }
}

/** An effect as printed (one decimal). */
export function rounded(pp: number): number {
  return Number(pp.toFixed(1))
}

/** Effects always carry one decimal, so a column of them lines up: "+10.3 pp", "±0.0 pp". */
export function formatEffect(pp: number): string {
  return `${formatSigned(pp, 1)} pp`
}

/** A dimension in running text: "prompt", "untrusted-output isolation". */
export function dimensionName(dimension: ConfigDimension): string {
  return DIMENSION_LABELS[dimension].toLowerCase()
}

/** "A + prompt", "A + prompt + graph" (forward) or "B − prompt" (backward). */
export function variantName(direction: AblationDirection, dimensions: readonly ConfigDimension[]): string {
  return `${direction === 'forward' ? 'A + ' : 'B − '}${dimensions.map(dimensionName).join(' + ')}`
}

export function variantOf(ablation: Ablation, dimension: ConfigDimension, direction: AblationDirection): AblationVariant | null {
  return ablation.variants.find((v) => v.dimension === dimension && v.direction === direction) ?? null
}

/** The `runs` key of a pair of dimensions: "pair:" plus both in configuration order. */
export function pairKey(ablation: Ablation, dimensions: readonly ConfigDimension[]): string {
  const at = (d: ConfigDimension) => ablation.dimensions.indexOf(d)
  return `pair:${[...dimensions].sort((x, y) => at(x) - at(y)).join('+')}`
}

export interface Interaction {
  pair: AblationPair
  /** The forward effect of each dimension alone, in the pair's order (null if not reported). */
  solo: [number | null, number | null]
  reading: string
}

/**
 * How a pair combines, against its two solo effects: only together (neither changes the
 * pass rate alone), overlapping (both help alone, and together they do what the better one
 * does), or more or less than the two solo effects added up. The interaction compares the
 * pair with the sum of its solo effects, not with either one: a pair can do better than
 * each dimension alone and still less than both added up.
 */
function pairReading(pair: AblationPair, [first, second]: [number | null, number | null]): string {
  if (first !== null && second !== null) {
    const [s1, s2, together] = [rounded(first), rounded(second), rounded(pair.effect_pp)]
    if (s1 === 0 && s2 === 0) return 'only together'
    if (s1 > 0 && s2 > 0 && together === Math.max(s1, s2)) return 'overlap: either one is enough'
  }
  return `${pair.interaction_pp > 0 ? 'more' : 'less'} together than their solo effects add up to`
}

/** Pairs whose interaction is not zero as printed, the largest (in either sign) first. */
export function interactions(ablation: Ablation): Interaction[] {
  const solo = (d: ConfigDimension) => variantOf(ablation, d, 'forward')?.effect_pp ?? null
  return ablation.pairs
    .filter((pair) => rounded(pair.interaction_pp) !== 0)
    .map((pair) => {
      const effects: [number | null, number | null] = [solo(pair.dimensions[0]), solo(pair.dimensions[1])]
      return { pair, solo: effects, reading: pairReading(pair, effects) }
    })
    .sort((x, y) => Math.abs(y.pair.interaction_pp) - Math.abs(x.pair.interaction_pp))
}

/** One sentence on how much of the total change the dimensions explain one at a time. */
export function overallReading(ablation: Ablation): string {
  const total = rounded(ablation.total_effect_pp)
  const sum = rounded(ablation.sum_of_solo_effects_pp)
  const interaction = rounded(ablation.interaction_pp)
  const soloEffects = ablation.variants.filter((v) => v.direction === 'forward').map((v) => rounded(v.effect_pp))
  if (total === 0 && sum === 0 && soloEffects.every((e) => e === 0)) {
    return 'No dimension changes the pass rate, alone or all together.'
  }
  if (total === 0 && interaction === 0) return 'The dimensions cancel out: their effects one at a time add up to no change.'
  if (interaction === 0) return 'The dimensions add up: their effects one at a time account for the whole change.'
  if (total !== 0 && Math.sign(interaction) === Math.sign(total)) {
    return Math.abs(interaction) >= Math.abs(total) / 2
      ? 'Most of the change only appears when dimensions are combined.'
      : 'Part of the change only appears when dimensions are combined.'
  }
  return 'The dimensions overlap or cancel out: one at a time they add up to more than the change together.'
}

const DOMAINS = [10, 20, 30, 40, 50, 60, 80, 100]

/** The half-width of a diverging axis, in pp, that holds every value. */
export function effectDomain(values: number[]): number {
  const largest = rounded(Math.max(0, ...values.map(Math.abs)))
  return DOMAINS.find((d) => d >= largest) ?? 100
}
