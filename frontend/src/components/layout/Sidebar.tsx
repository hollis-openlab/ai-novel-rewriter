import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  BookOpen,
  Settings,
  Server,
  ChevronLeft,
  Activity,
  Moon,
  Sun
} from 'lucide-react'
import { useWorkerStore } from '../../stores'
import { clsx } from 'clsx'

interface SidebarProps {
  collapsed: boolean
  onToggleCollapse: () => void
  darkMode: boolean
  onToggleDarkMode: () => void
}

const navItems = [
  { path: '/', icon: LayoutDashboard, label: 'Dashboard', label_zh: '仪表盘' },
  { path: '/novels', icon: BookOpen, label: 'Novels', label_zh: '小说管理' },
  { path: '/config', icon: Settings, label: 'Config', label_zh: '配置' },
  { path: '/providers', icon: Server, label: 'Providers', label_zh: '模型配置' },
]

export function Sidebar({ collapsed, onToggleCollapse, darkMode, onToggleDarkMode }: SidebarProps) {
  const { active, idle, queue_size } = useWorkerStore()

  return (
    <div className={clsx(
      'bg-page dark:bg-dark-page h-full flex flex-col transition-all duration-200 border-r border-transparent dark:border-dark-border',
      collapsed ? 'w-sidebar-collapsed' : 'w-sidebar'
    )}>
      {/* Header with Logo and Collapse Button */}
      <div className="p-6 flex items-center justify-between">
        {!collapsed && (
          <div className="flex items-center space-x-2">
            <div className="w-8 h-8 bg-accent rounded-lg flex items-center justify-center">
              <BookOpen className="w-4 h-4 text-white" strokeWidth={1.5} />
            </div>
            <h1 className="text-title-3 font-semibold text-primary">AI Novel</h1>
          </div>
        )}

        {!collapsed && (
          <button
            onClick={onToggleDarkMode}
            className="p-2 rounded-lg hover:bg-subtle dark:hover:bg-dark-subtle transition-colors duration-150 cursor-pointer"
            title={darkMode ? '切换到浅色模式' : '切换到深色模式'}
          >
            {darkMode ? (
              <Sun className="w-4 h-4 text-secondary dark:text-dark-secondary" strokeWidth={1.5} />
            ) : (
              <Moon className="w-4 h-4 text-secondary dark:text-dark-secondary" strokeWidth={1.5} />
            )}
          </button>
        )}

        <button
          onClick={onToggleCollapse}
          className={clsx(
            'p-2 rounded-lg hover:bg-subtle dark:hover:bg-dark-subtle transition-colors duration-150 cursor-pointer',
            collapsed && 'mx-auto'
          )}
        >
          <ChevronLeft
            className={clsx(
              'w-5 h-5 text-secondary dark:text-dark-secondary transition-transform duration-200',
              collapsed && 'rotate-180'
            )}
            strokeWidth={1.5}
          />
        </button>
      </div>

      {/* Navigation Items */}
      <nav className="flex-1 px-4 space-y-2">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              clsx(
                'nav-item flex items-center',
                isActive && 'nav-item-active',
                collapsed ? 'justify-center' : 'space-x-3'
              )
            }
          >
            <item.icon
              className="w-5 h-5 flex-shrink-0"
              strokeWidth={1.5}
            />
            {!collapsed && (
              <span className="text-body font-medium">
                {item.label_zh}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Worker Pool Mini Monitor */}
      <div className="p-4 border-t border-border">
        {collapsed ? (
          <div className="flex flex-col items-center gap-2">
            <div className="p-2 rounded-lg bg-subtle dark:bg-dark-subtle">
              <Activity className="w-4 h-4 text-secondary dark:text-dark-secondary" strokeWidth={1.5} />
            </div>
            <button
              onClick={onToggleDarkMode}
              className="p-2 rounded-lg hover:bg-subtle dark:hover:bg-dark-subtle transition-colors duration-150 cursor-pointer"
              title={darkMode ? '切换到浅色模式' : '切换到深色模式'}
            >
              {darkMode ? (
                <Sun className="w-4 h-4 text-secondary dark:text-dark-secondary" strokeWidth={1.5} />
              ) : (
                <Moon className="w-4 h-4 text-secondary dark:text-dark-secondary" strokeWidth={1.5} />
              )}
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <h3 className="text-caption font-semibold text-secondary uppercase tracking-wide">
              Worker Pool
            </h3>

            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <span className="text-callout text-secondary">Active</span>
                <span className="text-callout font-medium text-primary">{active}</span>
              </div>

              <div className="flex justify-between items-center">
                <span className="text-callout text-secondary">Idle</span>
                <span className="text-callout font-medium text-primary">{idle}</span>
              </div>

              {queue_size > 0 && (
                <div className="flex justify-between items-center">
                  <span className="text-callout text-secondary">Queue</span>
                  <span className="text-callout font-medium text-warning">{queue_size}</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
