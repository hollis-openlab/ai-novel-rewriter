import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { Novels } from './pages/Novels'
import { NovelDetail } from './pages/NovelDetail'
import { ChapterEditor } from './pages/ChapterEditor'
import { Config } from './pages/Config'
import { Providers } from './pages/Providers'

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/novels" element={<Novels />} />
          <Route path="/novels/:id" element={<NovelDetail />} />
          <Route path="/novels/:id/chapters/:chapterId" element={<ChapterEditor />} />
          <Route path="/config" element={<Config />} />
          <Route path="/providers" element={<Providers />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
