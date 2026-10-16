<script lang="ts">
export interface TextSegment {
  kind: 'text' | 'open' | 'close' | 'untrusted'
  text: string
}

const FRAME = /(<tool_output\b[^>]*>|<\/tool_output>)/

/**
 * Splits model input into plain text, untrusted-frame tags and framed content, so the
 * frames are visible. Every segment is rendered as a text node.
 */
export function splitUntrusted(text: string): TextSegment[] {
  let inside = false
  return text
    .split(FRAME)
    .filter((part) => part !== '')
    .map((part) => {
      if (part.startsWith('<tool_output')) {
        inside = true
        return { kind: 'open', text: part }
      }
      if (part === '</tool_output>') {
        inside = false
        return { kind: 'close', text: part }
      }
      return { kind: inside ? 'untrusted' : 'text', text: part }
    })
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import type { JsonValue } from '@/api/types'

const props = defineProps<{ messages: JsonValue }>()

interface Message {
  role: string
  content: string
}

const list = computed<Message[]>(() =>
  Array.isArray(props.messages)
    ? props.messages.map((m) => {
        const record = m && typeof m === 'object' && !Array.isArray(m) ? m : {}
        return {
          role: typeof record.role === 'string' ? record.role : 'unknown',
          content:
            typeof record.content === 'string' ? record.content : (JSON.stringify(record.content) ?? '(no content)'),
        }
      })
    : [],
)
</script>

<template>
  <ol class="messages">
    <li v-for="(message, i) in list" :key="i" class="message" :class="message.role">
      <details v-if="message.role === 'system'" class="system">
        <summary>
          <span class="role">system</span>
          <span class="faint">{{ message.content.length.toLocaleString() }} characters · show prompt</span>
        </summary>
        <pre class="content mono">{{ message.content }}</pre>
      </details>
      <template v-else>
        <span class="role">{{ message.role }}</span>
        <pre class="content mono"><span
          v-for="(segment, j) in splitUntrusted(message.content)"
          :key="j"
          :class="segment.kind"
        >{{ segment.text }}</span></pre>
      </template>
    </li>
  </ol>
</template>

<style scoped>
.messages {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.message {
  padding: 6px 10px 8px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface-1);
}
.message.assistant {
  border-left-color: var(--kind-llm);
}
.message.tool {
  border-left-color: var(--kind-tool);
}
.role {
  display: inline-block;
  margin-bottom: 2px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text-muted);
}
summary {
  display: flex;
  gap: 8px;
  align-items: baseline;
  cursor: pointer;
  font-size: 12px;
}
.content {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text);
}
.open,
.close {
  padding: 0 3px;
  border-radius: var(--radius-sm);
  background: var(--warn-soft);
  color: var(--warn);
  font-weight: 600;
}
.untrusted {
  background: var(--surface-2);
  box-shadow: inset 0 -1px 0 var(--warn);
}
</style>
