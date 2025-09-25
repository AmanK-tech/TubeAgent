import React from 'react'
import ReactDOM from 'react-dom/client'
import './styles/globals.css'
import { Providers } from './app/providers'
import { AppRoutes } from './app/routes'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Providers>
      <AppRoutes />
    </Providers>
  </React.StrictMode>
)

