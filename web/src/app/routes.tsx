import React from 'react'
import { BrowserRouter, Route, Routes, Navigate } from 'react-router-dom'
import { ChatPage } from '../pages/ChatPage'

export const AppRoutes: React.FC = () => {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
      </Routes>
    </BrowserRouter>
  )
}
