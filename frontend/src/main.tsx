import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './i18n'
import './index.css'

// Create a query client instance for React Query
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: (failureCount, error) => {
        // Don't retry on 4xx errors except 401/403 (auth issues)
        if (error instanceof Error && 'status' in error) {
          const status = (error as any).status
          if (status >= 400 && status < 500 && status !== 401 && status !== 403) {
            return false
          }
        }
        return failureCount < 3
      },
    },
  },
})

// Initialize the React app
const container = document.getElementById('root')
if (!container) {
  throw new Error('Root container not found')
}

const root = createRoot(container)

root.render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>
)