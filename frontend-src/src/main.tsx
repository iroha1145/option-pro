import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './styles/transitions-root.css'
import './index.css'
import './styles/transitions-catalog.css'
import { applyColorMode } from './lib/colorPreference.ts'
import { applyAppearance } from './lib/themePreference.ts'
import App from './App.tsx'

applyAppearance()
applyColorMode()

createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
)
