import { ArrowUp, Square } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/cn'

const MAX_ROWS = 12

interface ComposerProps {
  onSend: (prompt: string) => void
  onStop: () => void
  busy: boolean
  disabled?: boolean
  placeholder?: string
}

/**
 * The input.
 *
 * Grows with its content up to a ceiling, then scrolls. Without a ceiling a
 * pasted document pushes the conversation off the screen; without growth a
 * three-line question is typed through a one-line slot.
 */
export function Composer({
  onSend,
  onStop,
  busy,
  disabled = false,
  placeholder = 'Ask anything',
}: ComposerProps) {
  const [value, setValue] = useState('')
  const field = useRef<HTMLTextAreaElement>(null)

  const resize = useCallback(() => {
    const element = field.current
    if (!element) return
    // Reset first: without it the height only ever grows, because scrollHeight
    // of an already-tall box never reports a smaller content.
    element.style.height = 'auto'
    const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight) || 20
    element.style.height = `${Math.min(element.scrollHeight, lineHeight * MAX_ROWS)}px`
  }, [])

  useEffect(resize, [value, resize])

  const submit = useCallback(() => {
    const prompt = value.trim()
    if (!prompt || busy || disabled) return
    onSend(prompt)
    setValue('')
  }, [value, busy, disabled, onSend])

  return (
    <div
      className={cn(
        'flex items-end gap-2 rounded-lg border border-border bg-surface p-2',
        'focus-within:border-accent transition-colors',
        disabled && 'opacity-60',
      )}
    >
      <textarea
        ref={field}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          // Enter sends; Shift+Enter adds a line. The other way round is a
          // reasonable default for a document editor and the wrong one here,
          // where most messages are a sentence.
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            submit()
          }
        }}
        rows={1}
        disabled={disabled}
        placeholder={placeholder}
        aria-label="Message"
        className={cn(
          'flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-5',
          'text-ink placeholder:text-ink-faint focus:outline-none',
        )}
      />

      {busy ? (
        <Button variant="secondary" size="icon" onClick={onStop} aria-label="Stop">
          <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
        </Button>
      ) : (
        <Button
          variant="primary"
          size="icon"
          onClick={submit}
          disabled={value.trim().length === 0 || disabled}
          aria-label="Send"
        >
          <ArrowUp className="h-4 w-4" aria-hidden="true" />
        </Button>
      )}
    </div>
  )
}
