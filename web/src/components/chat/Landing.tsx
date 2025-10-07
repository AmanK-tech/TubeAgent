import React from 'react'

export const Landing: React.FC = () => {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div className="landing-bg" />
      <div className="w-full max-w-3xl px-6 text-center">
        <h1 className="text-6xl font-semibold tracking-tight text-white/95 mb-8 animate-fade-in-up [animation-delay:80ms] drop-shadow-[0_2px_20px_rgba(99,102,241,.35)]">TubeAgent</h1>
      </div>
    </div>
  )
}
