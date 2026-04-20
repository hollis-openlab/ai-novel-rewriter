import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import zhCommon from './locales/zh/common.json'
import zhDashboard from './locales/zh/dashboard.json'
import zhNovels from './locales/zh/novels.json'
import zhNovelDetail from './locales/zh/novelDetail.json'
import zhChapterEditor from './locales/zh/chapterEditor.json'
import zhConfig from './locales/zh/config.json'
import zhProviders from './locales/zh/providers.json'

import enCommon from './locales/en/common.json'
import enDashboard from './locales/en/dashboard.json'
import enNovels from './locales/en/novels.json'
import enNovelDetail from './locales/en/novelDetail.json'
import enChapterEditor from './locales/en/chapterEditor.json'
import enConfig from './locales/en/config.json'
import enProviders from './locales/en/providers.json'

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      zh: {
        common: zhCommon,
        dashboard: zhDashboard,
        novels: zhNovels,
        novelDetail: zhNovelDetail,
        chapterEditor: zhChapterEditor,
        config: zhConfig,
        providers: zhProviders,
      },
      en: {
        common: enCommon,
        dashboard: enDashboard,
        novels: enNovels,
        novelDetail: enNovelDetail,
        chapterEditor: enChapterEditor,
        config: enConfig,
        providers: enProviders,
      },
    },
    fallbackLng: 'zh',
    defaultNS: 'common',
    ns: ['common', 'dashboard', 'novels', 'novelDetail', 'chapterEditor', 'config', 'providers'],
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'i18nextLng',
    },
  })

export default i18n
