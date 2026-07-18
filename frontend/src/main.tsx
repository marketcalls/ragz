import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import './styles/globals.css';

import React from 'react';
import ReactDOM from 'react-dom/client';

import { App } from './app';
import { applyTheme, resolveInitialTheme } from './lib/theme';

applyTheme(resolveInitialTheme());

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
