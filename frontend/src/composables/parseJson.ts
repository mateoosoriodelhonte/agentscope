/** One problem in user input, located by a JSON pointer. */
export interface FieldIssue {
  path: string
  message: string
}

/** Parses JSON text, reporting a syntax error as an issue at the document root. */
export function parseJson(source: string): { value: unknown; issues: FieldIssue[] } {
  try {
    return { value: JSON.parse(source) as unknown, issues: [] }
  } catch (e) {
    return { value: undefined, issues: [{ path: '/', message: e instanceof Error ? e.message : String(e) }] }
  }
}
