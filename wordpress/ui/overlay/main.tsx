import React from 'react';
import ReactDOM from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import App from './App';
import { nativeRuntime } from './native/runtime';
import './index.css';

// Never register the original root-scoped service worker on a WordPress website.
const root=ReactDOM.createRoot(document.getElementById('root')!);
try {
  nativeRuntime();
  root.render(<React.StrictMode>
    <HashRouter><App/><Toaster position="top-right"/></HashRouter>
  </React.StrictMode>);
} catch(error) {
  root.render(<main style={{padding:32}}><h1>WordPress application configuration needed</h1><p>{error instanceof Error?error.message:'Native runtime unavailable.'}</p></main>);
}
