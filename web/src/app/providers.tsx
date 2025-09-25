import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

const qc = new QueryClient()

export const Providers: React.FC<React.PropsWithChildren> = ({ children }) => {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

