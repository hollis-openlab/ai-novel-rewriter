import { useState, useEffect } from 'react'
import { Sidebar } from './Sidebar'

interface AppShellProps {
  children: React.ReactNode
}

export function AppShell({ children }: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [darkMode, setDarkMode] = useState(false)

  useEffect(() => {
    const stored = window.localStorage.getItem('ai-novel-dark-mode')
    if (stored === '1') {
      setDarkMode(true)
      document.documentElement.classList.add('dark')
    }
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode)
    window.localStorage.setItem('ai-novel-dark-mode', darkMode ? '1' : '0')
  }, [darkMode])

  // Auto-collapse sidebar on smaller screens
  useEffect(() => {
    const handleResize = () => {
      const shouldCollapse = window.innerWidth < 1280
      setSidebarCollapsed(shouldCollapse)
    }

    // Set initial state
    handleResize()

    // Listen for window resize
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  const handleToggleCollapse = () => {
    setSidebarCollapsed(!sidebarCollapsed)
  }

  return (
    <div className="flex h-screen bg-page dark:bg-dark-page">
      {/* Sidebar */}
      <div className="flex-shrink-0 h-full">
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggleCollapse={handleToggleCollapse}
          darkMode={darkMode}
          onToggleDarkMode={() => setDarkMode((prev) => !prev)}
        />
      </div>

      {/* Main Content Area */}
      <div className="flex-1 h-full overflow-hidden">
        <main className="h-full bg-white dark:bg-dark-card rounded-2xl m-3 md:m-4 ml-0 overflow-auto border border-transparent dark:border-dark-border">
          <div className="p-4 lg:p-8 h-full max-w-[1680px] mx-auto">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
