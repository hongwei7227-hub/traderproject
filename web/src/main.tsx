import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from '@/App'
import { SessionProvider } from '@/contexts/SessionContext'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { assertConfigured } from '@/lib/env'
import { createQueryClient } from '@/lib/queryClient'

import './index.css'

// Fails here rather than three screens in. A platform build with no auth
// provider would otherwise serve every request as the same account and look
// perfectly healthy doing it.
assertConfigured()

const root = document.getElementById('root')
if (!root) {
  throw new Error('No #root element — index.html and this entry disagree.')
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={createQueryClient()}>
      <BrowserRouter>
        <SessionProvider>
          <ThemeProvider>
            <App />
          </ThemeProvider>
        </SessionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
