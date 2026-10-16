import { ref } from 'vue'

/**
 * A detail label for the current page (e.g. the run being viewed), shown as the last
 * breadcrumb and in document.title. The router clears it on every path change.
 */
export const pageDetail = ref<string | null>(null)

export function setPageDetail(label: string | null): void {
  pageDetail.value = label
}
