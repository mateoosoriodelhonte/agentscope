import { ref } from 'vue'

export interface Crumb {
  label: string
  to?: string
}

/**
 * A detail label for the current page (e.g. the run being viewed), shown as the last
 * breadcrumb and in document.title. The router clears it on every path change.
 */
export const pageDetail = ref<string | null>(null)

/**
 * Breadcrumbs between the section's list page and the current page, e.g. suite › case for
 * a trace opened from an evaluation. Cleared on every path change, like the detail.
 */
export const pageTrail = ref<Crumb[]>([])

export function setPageDetail(label: string | null): void {
  pageDetail.value = label
}

export function setPageTrail(crumbs: Crumb[]): void {
  pageTrail.value = crumbs
}
