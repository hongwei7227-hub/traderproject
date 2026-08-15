/**
 * The row that assigns a model to a role.
 *
 * Most of what is worth testing here is what the row refuses to do: offer a
 * model that cannot fill the role, hide a stored choice the deployment has
 * since withdrawn, or show a new value before the server has accepted it.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { RoleRow } from '../RoleRow'

import { ApiError } from '@/api/client'
import type { ModelView, RoleAssignment } from '@/types/api'

const mutate = vi.fn()

vi.mock('@/hooks/useModelSettings', () => ({
  useSetPreference: () => ({ mutate, isPending: false }),
}))

function model(overrides: Partial<ModelView> = {}): ModelView {
  return {
    id: 'flagship',
    provider: 'vendor',
    provider_name: 'Vendor Inc.',
    wire: 'anthropic-messages',
    context: 200_000,
    max_output: 8_000,
    capabilities: ['text', 'tool_calling', 'streaming', 'vision'],
    eligible_roles: ['primary', 'swift', 'condense', 'extract'],
    ...overrides,
  }
}

function assignment(overrides: Partial<RoleAssignment> = {}): RoleAssignment {
  return {
    role: 'swift',
    model_id: 'flagship',
    decided_by: 'system-baseline',
    overridden: false,
    requires: ['text', 'tool_calling', 'streaming'],
    ...overrides,
  }
}

function draw(element: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)
}

beforeEach(() => {
  mutate.mockReset()
})

describe('provenance', () => {
  it('says when a role is running on the platform default', () => {
    // A row showing a model name is ambiguous between a deliberate choice and
    // a default that looks like one, and the two behave differently when the
    // deployment changes underneath.
    draw(<RoleRow assignment={assignment()} models={[model()]} />)
    expect(screen.getByText(/platform default/i)).toBeInTheDocument()
  })

  it('says when the choice is the account holder’s own', () => {
    draw(
      <RoleRow
        assignment={assignment({ decided_by: 'tenant-preference', overridden: true })}
        models={[model()]}
      />,
    )
    expect(screen.getByText(/your choice/i)).toBeInTheDocument()
  })

  it('falls back to the raw name for a level it has no wording for', () => {
    draw(
      <RoleRow assignment={assignment({ decided_by: 'org-policy' })} models={[model()]} />,
    )
    expect(screen.getByText(/org-policy/)).toBeInTheDocument()
  })
})

describe('the choices offered', () => {
  it('lists the models it was given', () => {
    draw(
      <RoleRow
        assignment={assignment()}
        models={[model(), model({ id: 'cheap' })]}
      />,
    )
    expect(screen.getAllByRole('option')).toHaveLength(2)
  })

  it('keeps a stored model that is no longer offered, marked as such', () => {
    // Snapping the select to something else would leave someone believing they
    // had configured a model they had not.
    draw(
      <RoleRow
        assignment={assignment({ model_id: 'withdrawn', overridden: true })}
        models={[model()]}
      />,
    )
    expect(screen.getByRole('option', { name: /withdrawn \(unavailable\)/ })).toBeInTheDocument()
  })

  it('does not invent an unavailable entry for a baseline it cannot see', () => {
    // An unset role resolves server-side and may name a model the tenant is not
    // offered. That is not a withdrawn choice, and labelling it as one would be
    // a warning about nothing.
    draw(
      <RoleRow assignment={assignment({ model_id: 'internal' })} models={[model()]} />,
    )
    expect(screen.queryByText(/unavailable/)).not.toBeInTheDocument()
  })
})

describe('changing the assignment', () => {
  it('sends the chosen model', async () => {
    draw(
      <RoleRow
        assignment={assignment()}
        models={[model(), model({ id: 'cheap' })]}
      />,
    )
    await userEvent.selectOptions(screen.getByRole('combobox'), 'cheap')

    expect(mutate).toHaveBeenCalledWith(
      { role: 'swift', modelId: 'cheap' },
      expect.anything(),
    )
  })

  it('sends null to clear an override', async () => {
    draw(
      <RoleRow assignment={assignment({ overridden: true })} models={[model()]} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /reset/i }))

    expect(mutate).toHaveBeenCalledWith(
      { role: 'swift', modelId: null },
      expect.anything(),
    )
  })

  it('offers no reset when there is nothing to reset', () => {
    draw(<RoleRow assignment={assignment({ overridden: false })} models={[model()]} />)
    expect(screen.queryByRole('button', { name: /reset/i })).not.toBeInTheDocument()
  })

  it('shows the server’s reason when a choice is refused', async () => {
    mutate.mockImplementation(
      (
        _change: unknown,
        options: { onError: (error: unknown) => void },
      ) => {
        options.onError(
          new ApiError(422, "Model 'cheap' cannot fill role 'primary'; it lacks vision"),
        )
      },
    )

    draw(
      <RoleRow
        assignment={assignment({ role: 'primary' })}
        models={[model(), model({ id: 'cheap' })]}
      />,
    )
    await userEvent.selectOptions(screen.getByRole('combobox'), 'cheap')

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/lacks vision/)
    })
  })

  it('does not show the new value before the server accepts it', async () => {
    // The server can refuse. Rendering the choice first would mean showing
    // something that never took, then taking it back.
    mutate.mockImplementation(() => {
      /* still in flight */
    })

    draw(
      <RoleRow
        assignment={assignment()}
        models={[model(), model({ id: 'cheap' })]}
      />,
    )
    await userEvent.selectOptions(screen.getByRole('combobox'), 'cheap')

    expect(screen.getByRole('combobox')).toHaveValue('flagship')
  })
})
