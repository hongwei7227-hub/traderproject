/**
 * What a reader sees, including the states that are easy to render wrongly.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Composer } from '../Composer'
import { ToolCallCard } from '../ToolCallCard'
import { TurnView } from '../TurnView'

import { emptyTurn, type ToolCall, type TurnState } from '@/lib/turn/state'

function turn(overrides: Partial<TurnState> = {}): TurnState {
  return { ...emptyTurn(), ...overrides }
}

function call(overrides: Partial<ToolCall> = {}): ToolCall {
  return { callId: 'c1', name: 'search', arguments: {}, ...overrides }
}

describe('answer', () => {
  it('shows the text', () => {
    render(<TurnView turn={turn({ text: 'Revenue grew 12%.' })} />)
    expect(screen.getByText(/Revenue grew 12%/)).toBeInTheDocument()
  })

  it('marks a truncated answer as such', () => {
    // A budget-stopped answer reads exactly like a finished one. Saying so is
    // the difference between a limit and a defect.
    render(<TurnView turn={turn({ text: 'Partial', stopReason: 'iteration_budget' })} />)
    expect(screen.getByText(/stopped early/i)).toBeInTheDocument()
  })

  it('does not mark a completed answer', () => {
    render(<TurnView turn={turn({ text: 'Done', stopReason: 'answered' })} />)
    expect(screen.queryByText(/stopped early/i)).not.toBeInTheDocument()
  })

  it('does not mark a cancelled answer', () => {
    // The reader stopped it and already knows why.
    render(<TurnView turn={turn({ text: 'Partial', stopReason: 'cancelled' })} />)
    expect(screen.queryByText(/stopped early/i)).not.toBeInTheDocument()
  })
})

describe('notices', () => {
  it('shows them above the answer', () => {
    // They change how to read what follows, so they have to arrive first.
    render(
      <TurnView
        turn={turn({
          text: 'Answer',
          notices: [{ message: 'answered by a fallback model', severity: 'warning' }],
        })}
      />,
    )
    const notice = screen.getByText(/fallback model/)
    const answer = screen.getByText('Answer')
    expect(notice.compareDocumentPosition(answer)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('announces an error assertively', () => {
    // A reader watching the answer is not looking at this part of the page.
    render(<TurnView turn={turn({ error: { message: 'Upstream refused', retryable: true } })} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Upstream refused')
  })

  it('says when an error is worth retrying', () => {
    render(<TurnView turn={turn({ error: { message: 'Timed out', retryable: true } })} />)
    expect(screen.getByRole('alert')).toHaveTextContent(/trying again/)
  })
})

describe('tool calls', () => {
  it('summarises how many steps ran', () => {
    render(<TurnView turn={turn({ toolCalls: [call(), call({ callId: 'c2' })] })} />)
    expect(screen.getByText('2 steps')).toBeInTheDocument()
  })

  it('uses the singular for one', () => {
    render(<TurnView turn={turn({ toolCalls: [call()] })} />)
    expect(screen.getByText('1 step')).toBeInTheDocument()
  })

  it('shows nothing when there were none', () => {
    render(<TurnView turn={turn({ text: 'Direct answer' })} />)
    expect(screen.queryByText(/step/)).not.toBeInTheDocument()
  })
})

describe('tool call card', () => {
  it('starts collapsed', () => {
    // A dozen expanded cards bury the answer they exist to support.
    render(<ToolCallCard call={call({ result: { ok: true, summary: 'found 3' } })} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'false')
  })

  it('opens a failed call by default', () => {
    // The one case where the detail explains the answer above it.
    render(<ToolCallCard call={call({ result: { ok: false, summary: 'not found' } })} />)
    expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  })

  it('expands on click', async () => {
    render(<ToolCallCard call={call({ arguments: { query: 'revenue' } })} />)
    await userEvent.click(screen.getByRole('button'))
    expect(screen.getByText('query')).toBeInTheDocument()
  })

  it('labels a running call', () => {
    render(<ToolCallCard call={call()} />)
    expect(screen.getByLabelText('Running')).toBeInTheDocument()
  })

  it('labels a finished call', () => {
    render(<ToolCallCard call={call({ result: { ok: true, summary: 'ok' } })} />)
    expect(screen.getByLabelText('Finished')).toBeInTheDocument()
  })
})

describe('composer', () => {
  it('sends on enter', async () => {
    const onSend = vi.fn()
    render(<Composer onSend={onSend} onStop={vi.fn()} busy={false} />)

    await userEvent.type(screen.getByLabelText('Message'), 'hello{Enter}')

    expect(onSend).toHaveBeenCalledWith('hello')
  })

  it('adds a line on shift+enter', async () => {
    const onSend = vi.fn()
    render(<Composer onSend={onSend} onStop={vi.fn()} busy={false} />)

    await userEvent.type(screen.getByLabelText('Message'), 'a{Shift>}{Enter}{/Shift}b')

    expect(onSend).not.toHaveBeenCalled()
  })

  it('refuses to send only whitespace', async () => {
    const onSend = vi.fn()
    render(<Composer onSend={onSend} onStop={vi.fn()} busy={false} />)

    await userEvent.type(screen.getByLabelText('Message'), '   {Enter}')

    expect(onSend).not.toHaveBeenCalled()
  })

  it('clears after sending', async () => {
    render(<Composer onSend={vi.fn()} onStop={vi.fn()} busy={false} />)
    const field = screen.getByLabelText('Message')

    await userEvent.type(field, 'question{Enter}')

    expect(field).toHaveValue('')
  })

  it('offers stop instead of send while busy', () => {
    render(<Composer onSend={vi.fn()} onStop={vi.fn()} busy />)
    expect(screen.getByLabelText('Stop')).toBeInTheDocument()
    expect(screen.queryByLabelText('Send')).not.toBeInTheDocument()
  })

  it('will not send while busy', async () => {
    const onSend = vi.fn()
    render(<Composer onSend={onSend} onStop={vi.fn()} busy />)

    await userEvent.type(screen.getByLabelText('Message'), 'hello{Enter}')

    expect(onSend).not.toHaveBeenCalled()
  })
})
