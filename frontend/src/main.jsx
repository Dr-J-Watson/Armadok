import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App.jsx'
import { AuthProvider } from './auth.jsx'
import { UiProvider } from './components/ui.jsx'
import { NodeProvider } from './node.jsx'
import './styles.css'

const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15000 } } })

createRoot(document.getElementById('root')).render(
  <QueryClientProvider client={qc}>
    <BrowserRouter>
      <AuthProvider>
        <UiProvider>
          <NodeProvider>
            <App />
          </NodeProvider>
        </UiProvider>
      </AuthProvider>
    </BrowserRouter>
  </QueryClientProvider>,
)
