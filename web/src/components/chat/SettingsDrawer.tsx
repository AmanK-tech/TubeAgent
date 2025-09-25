import React from 'react'

export const SettingsDrawer: React.FC = () => {
  return (
    <aside className="hidden md:block panel h-full border-l border-neutral-800 p-4 w-[320px]">
      <div className="text-sm font-medium text-neutral-300 mb-3">Settings</div>
      <div className="space-y-3 text-sm text-neutral-400">
        <div>
          <div className="text-neutral-500">Model</div>
          <div>deepseek-chat</div>
        </div>
        <div>
          <div className="text-neutral-500">Theme</div>
          <div>Dark</div>
        </div>
      </div>
    </aside>
  )
}

