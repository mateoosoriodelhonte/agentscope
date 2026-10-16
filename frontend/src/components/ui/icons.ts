/** 16×16 stroke icons as path data, drawn with currentColor by Icon.vue. */

function circle(cx: number, cy: number, r: number): string {
  return `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`
}

export const ICONS = {
  check: ['M3.5 8.5l3 3 6-7'],
  x: ['M4.5 4.5l7 7M11.5 4.5l-7 7'],
  alert: ['M8 2.5l6 11H2z', 'M8 6.5v3', 'M8 11.75v.01'],
  minus: ['M4 8h8'],
  dot: [circle(8, 8, 2.5)],
  clock: [circle(8, 8, 5.5), 'M8 5v3.25l2 1.25'],
  ban: [circle(8, 8, 5.5), 'M4.2 4.2l7.6 7.6'],
  retry: ['M12.5 8a4.5 4.5 0 1 1-1.3-3.2', 'M12 2.5v2.75H9.25'],
  chevronRight: ['M6 4l4 4-4 4'],
  chevronDown: ['M4 6l4 4 4-4'],
  copy: ['M5.5 5.5h7v7h-7z', 'M3.5 10.5v-7h7'],
  sun: [circle(8, 8, 2.75), 'M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1'],
  moon: ['M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z'],
  monitor: ['M2 3h12v8H2z', 'M6 14h4M8 11v3'],
  lock: ['M4 7.5h8v6H4z', 'M5.75 7.5V5.25a2.25 2.25 0 0 1 4.5 0V7.5'],
  scissors: [circle(4.5, 4.5, 1.75), circle(4.5, 11.5, 1.75), 'M6 5.5l7.5 6M6 10.5l7.5-6'],
  flask: ['M6 2h4M7 2v4.25L3.2 13a.6.6 0 0 0 .5.9h8.6a.6.6 0 0 0 .5-.9L9 6.25V2', 'M4.75 10.25h6.5'],
  keyboard: ['M1.5 4h13v8h-13z', 'M4 6.5h.01M6.5 6.5h.01M9 6.5h.01M11.5 6.5h.01M5 9.5h6'],
  overview: ['M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z'],
  runs: ['M2 4h12M2 8h12M2 12h8'],
  evaluations: ['M2 4l1.5 1.5L6 3M8.5 4.5H14M2 10l1.5 1.5L6 9M8.5 10.5H14'],
  compare: ['M3 5h9.5L10.5 3M13 11H3.5l2 2'],
  failures: ['M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z'],
  agents: ['M3 5h10v8H3z', 'M8 5V2.5', circle(6, 9, 0.6), circle(10, 9, 0.6)],
  datasets: [
    'M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2z',
    'M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4',
    'M3 8c0 1.1 2.2 2 5 2s5-.9 5-2',
  ],
  settings: [
    'M2 4h7M12 4h2M2 8h2M7 8h7M2 12h8M13 12h1',
    circle(10.5, 4, 1.5),
    circle(5.5, 8, 1.5),
    circle(11.5, 12, 1.5),
  ],
  filter: ['M2 3h12L9.5 8.5V13l-3-1.5v-3z'],
  play: ['M5 3.5v9l7-4.5z'],
} as const satisfies Record<string, readonly string[]>

export type IconName = keyof typeof ICONS
