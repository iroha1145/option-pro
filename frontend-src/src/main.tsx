import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './styles/transitions-root.css'
import './index.css'
import './styles/transitions-catalog.css'
import { applyColorMode } from './lib/colorPreference.ts'
import { applyAppearance } from './lib/themePreference.ts'
import { prepareI18n } from './i18n/boot.ts'
import { prefetchRouteChunk } from './lib/prefetchRouteChunk.ts'

applyAppearance()
applyColorMode()

void prepareI18n().then(async () => {
  prefetchRouteChunk(window.location.pathname)
  const { default: App } = await import('./App.tsx')
  createRoot(document.getElementById('root')!).render(
    <BrowserRouter>
      <App />
    </BrowserRouter>,
  )
})
