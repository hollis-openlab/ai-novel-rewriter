import { useTranslation } from 'react-i18next'
import { clsx } from 'clsx'

export function LanguageSwitcher({ collapsed }: { collapsed?: boolean }) {
  const { i18n } = useTranslation()
  const isZh = i18n.language?.startsWith('zh') ?? true

  const toggle = () => {
    i18n.changeLanguage(isZh ? 'en' : 'zh')
  }

  return (
    <button
      onClick={toggle}
      className={clsx(
        'rounded-lg px-2.5 py-1.5 text-caption font-medium transition-colors duration-150 cursor-pointer',
        'text-secondary hover:text-primary hover:bg-subtle dark:hover:bg-dark-subtle',
        collapsed && 'mx-auto'
      )}
      title={isZh ? 'Switch to English' : '切换到中文'}
    >
      {isZh ? 'EN' : '中'}
    </button>
  )
}
