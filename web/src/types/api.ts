/**
 * The shapes the server sends.
 *
 * Plain interfaces, not schemas. These describe a trusted peer whose contract
 * is enforced by its own tests; validating them again here would double the
 * places a field rename has to be made without catching anything the type
 * checker does not.
 *
 * Zod appears only where input is genuinely untrusted — what a person typed,
 * what came out of local storage — because that is where a malformed value is
 * a real possibility rather than a hypothetical one.
 */

export interface ThreadSummary {
  id: string
  title: string | null
  workspace_id: string
  updated_at: string
}

export interface ThreadPage {
  threads: ThreadSummary[]
  limit: number
  offset: number
}

export interface SendMessageBody {
  prompt: string
  model?: string
  max_iterations?: number
}

/**
 * Event kinds a turn emits.
 *
 * A union rather than a string, so that a `switch` over it is checked for
 * exhaustiveness — adding a kind to the server without handling it here
 * becomes a compile error instead of a silently ignored frame.
 */
export type EventKind =
  | 'metadata'
  | 'text'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'artifact'
  | 'usage'
  | 'notice'
  | 'error'
  | 'done'

export interface MetadataPayload {
  run_id: string
  thread_id?: string
  model?: string
}

export interface TextPayload {
  text: string
}

export interface ToolCallPayload {
  call_id: string
  name: string
  arguments: Record<string, unknown>
}

export interface ToolResultPayload {
  call_id: string
  ok: boolean
  summary: string
}

export interface ArtifactPayload {
  artifact_kind: string
  ref: string
  title?: string
}

export interface UsagePayload {
  input_tokens: number
  output_tokens: number
  model: string
}

export interface NoticePayload {
  message: string
  severity: 'info' | 'warning'
}

export interface ErrorPayload {
  message: string
  retryable?: boolean
  detail?: string
}

export interface DonePayload {
  phase: string
  reason: string
  characters?: number
}

/**
 * A parsed event, narrowed by kind.
 *
 * Discriminated so that reading `payload.text` is only possible after
 * establishing the kind is `text`.
 */
export type TurnEvent =
  | { kind: 'metadata'; seq: number; payload: MetadataPayload }
  | { kind: 'text'; seq: number; payload: TextPayload }
  | { kind: 'reasoning'; seq: number; payload: TextPayload }
  | { kind: 'tool_call'; seq: number; payload: ToolCallPayload }
  | { kind: 'tool_result'; seq: number; payload: ToolResultPayload }
  | { kind: 'artifact'; seq: number; payload: ArtifactPayload }
  | { kind: 'usage'; seq: number; payload: UsagePayload }
  | { kind: 'notice'; seq: number; payload: NoticePayload }
  | { kind: 'error'; seq: number; payload: ErrorPayload }
  | { kind: 'done'; seq: number; payload: DonePayload }

type Raw = Record<string, unknown>

function str(source: Raw, field: string, fallback = ''): string {
  const value = source[field]
  return typeof value === 'string' ? value : fallback
}

function num(source: Raw, field: string, fallback = 0): number {
  const value = source[field]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function bool(source: Raw, field: string, fallback = false): boolean {
  const value = source[field]
  return typeof value === 'boolean' ? value : fallback
}

function record(source: Raw, field: string): Record<string, unknown> {
  const value = source[field]
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/**
 * Narrow a raw frame into a typed event.
 *
 * Built field by field rather than asserted wholesale. A blanket assertion
 * would compile and then hand the interface a payload missing whatever the
 * server did not send, surfacing as an undefined three components later — the
 * exact failure mode that made the original's 83 `as unknown as` casts worth
 * removing.
 *
 * Unknown kinds return null. A newer server may emit kinds an older client has
 * never heard of, and ignoring one is better than breaking; the kinds that
 * matter for correctness have been in the protocol from the start.
 */
export function asTurnEvent(raw: {
  kind: string
  id: number | null
  data: Record<string, unknown>
}): TurnEvent | null {
  const seq = raw.id ?? 0
  const data = raw.data

  switch (raw.kind) {
    case 'metadata':
      return {
        kind: 'metadata',
        seq,
        payload: {
          run_id: str(data, 'run_id'),
          ...(typeof data['thread_id'] === 'string'
            ? { thread_id: data['thread_id'] }
            : {}),
          ...(typeof data['model'] === 'string' ? { model: data['model'] } : {}),
        },
      }

    case 'text':
    case 'reasoning':
      return { kind: raw.kind, seq, payload: { text: str(data, 'text') } }

    case 'tool_call':
      return {
        kind: 'tool_call',
        seq,
        payload: {
          call_id: str(data, 'call_id'),
          name: str(data, 'name'),
          arguments: record(data, 'arguments'),
        },
      }

    case 'tool_result':
      return {
        kind: 'tool_result',
        seq,
        payload: {
          call_id: str(data, 'call_id'),
          ok: bool(data, 'ok'),
          summary: str(data, 'summary'),
        },
      }

    case 'artifact':
      return {
        kind: 'artifact',
        seq,
        payload: {
          artifact_kind: str(data, 'artifact_kind'),
          ref: str(data, 'ref'),
          ...(typeof data['title'] === 'string' ? { title: data['title'] } : {}),
        },
      }

    case 'usage':
      return {
        kind: 'usage',
        seq,
        payload: {
          input_tokens: num(data, 'input_tokens'),
          output_tokens: num(data, 'output_tokens'),
          model: str(data, 'model'),
        },
      }

    case 'notice':
      return {
        kind: 'notice',
        seq,
        payload: {
          message: str(data, 'message'),
          severity: data['severity'] === 'warning' ? 'warning' : 'info',
        },
      }

    case 'error':
      return {
        kind: 'error',
        seq,
        payload: {
          message: str(data, 'message', 'Something went wrong.'),
          retryable: bool(data, 'retryable'),
          ...(typeof data['detail'] === 'string' ? { detail: data['detail'] } : {}),
        },
      }

    case 'done':
      return {
        kind: 'done',
        seq,
        payload: {
          phase: str(data, 'phase'),
          reason: str(data, 'reason'),
          ...(typeof data['characters'] === 'number'
            ? { characters: data['characters'] }
            : {}),
        },
      }

    default:
      return null
  }
}
