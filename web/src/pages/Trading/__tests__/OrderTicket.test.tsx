/**
 * The order ticket, and what it does with a refusal.
 *
 * The refusal path is the one worth testing. The server answers with every
 * limit that was breached and by how much; showing only the first would turn
 * correcting an order into a guessing game where each fix reveals the next
 * objection.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { OrderTicket } from '../OrderTicket'

const mutate = vi.fn()
let refusal: { refusals: string[]; detail: string[] } | null = null
let pending = false
let failed = false

vi.mock('@/hooks/useTrading', () => ({
  usePlaceOrder: () => ({ mutate, isPending: pending, isError: failed, error: null }),
  refusalOf: () => refusal,
}))

vi.mock('@/hooks/useMarket', () => ({
  useAnalystRating: () => ({ isPending: false, isError: true, error: null, data: null }),
  isDependencyDown: () => false,
  isUncovered: () => false,
}))

function draw(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)
}

async function fill(symbol = 'NVDA', quantity = '10', price = '100') {
  await userEvent.type(screen.getByLabelText('Symbol'), symbol)
  await userEvent.type(screen.getByLabelText('Shares'), quantity)
  await userEvent.type(screen.getByLabelText(/price/i), price)
}

beforeEach(() => {
  mutate.mockReset()
  refusal = null
  pending = false
  failed = false
})

describe('submitting', () => {
  it('sends the symbol uppercased', async () => {
    draw(<OrderTicket />)
    await fill('nvda')
    await userEvent.click(screen.getByRole('button', { name: /buy/i }))

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ symbol: 'NVDA' }),
      expect.anything(),
    )
  })

  it('sends a limit price and no reference price for a limit order', async () => {
    // Sending both invites the two disagreeing, and the server refuses it.
    draw(<OrderTicket />)
    await fill()
    await userEvent.click(screen.getByRole('button', { name: /buy/i }))

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ limit_price: '100', reference_price: null }),
      expect.anything(),
    )
  })

  it('sends a reference price and no limit for a market order', async () => {
    draw(<OrderTicket />)
    await userEvent.click(screen.getByRole('radio', { name: 'Market' }))
    await fill()
    await userEvent.click(screen.getByRole('button', { name: /buy/i }))

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ limit_price: null, reference_price: '100' }),
      expect.anything(),
    )
  })

  it('explains why a market order still needs a price', async () => {
    draw(<OrderTicket />)
    await userEvent.click(screen.getByRole('radio', { name: 'Market' }))
    expect(screen.getByText(/still needs a price/i)).toBeInTheDocument()
  })

  it('will not submit without a symbol, a quantity and a price', () => {
    draw(<OrderTicket />)
    expect(screen.getByRole('button', { name: /buy/i })).toBeDisabled()
  })

  it('sends the rationale so it outlives the conversation', async () => {
    draw(<OrderTicket />)
    await fill()
    await userEvent.type(screen.getByLabelText('Rationale'), 'datacenter beat')
    await userEvent.click(screen.getByRole('button', { name: /buy/i }))

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ rationale: 'datacenter beat' }),
      expect.anything(),
    )
  })

  it('switches to selling', async () => {
    draw(<OrderTicket />)
    await userEvent.click(screen.getByRole('radio', { name: 'Sell' }))
    await fill()

    expect(screen.getByRole('button', { name: /sell/i })).toBeEnabled()
  })
})

describe('refusal', () => {
  it('names the limit that was hit', () => {
    refusal = {
      refusals: ['order_too_large'],
      detail: ['order is 10.0% of equity, limit is 2.0%'],
    }
    draw(<OrderTicket />)

    expect(screen.getByRole('alert')).toHaveTextContent('Order too large')
    expect(screen.getByRole('alert')).toHaveTextContent('10.0% of equity')
  })

  it('shows every breach rather than only the first', () => {
    refusal = {
      refusals: ['not_in_universe', 'daily_limit_reached'],
      detail: ['TSLA is not in the tradable universe', '3 orders already placed today'],
    }
    draw(<OrderTicket />)

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Symbol not tradable')
    expect(alert).toHaveTextContent('Daily order limit reached')
  })

  it('falls back to the raw code for a refusal it has no wording for', () => {
    // A newer server can refuse for a reason this build has never heard of.
    // Showing the code is worse than a sentence and much better than nothing.
    refusal = { refusals: ['margin_call'], detail: ['see your broker'] }
    draw(<OrderTicket />)

    expect(screen.getByRole('alert')).toHaveTextContent('margin_call')
  })
})

describe('acceptance', () => {
  it('says the order is queued rather than placed', async () => {
    // It is not placed. The broker sees it once the worker picks it up, and
    // saying otherwise would be claiming an outcome nobody has confirmed.
    mutate.mockImplementation(
      (_body: unknown, options: { onSuccess: (r: { proposal_id: string }) => void }) => {
        options.onSuccess({ proposal_id: 'p-1' })
      },
    )

    draw(<OrderTicket />)
    await fill()
    await userEvent.click(screen.getByRole('button', { name: /buy/i }))

    expect(screen.getByRole('status')).toHaveTextContent(/queued/i)
  })
})
