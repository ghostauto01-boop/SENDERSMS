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
  const runtime=nativeRuntime();
  root.render(<React.StrictMode>
    <div role="note" style={{padding:'8px 16px',background:'#fff4cc',color:'#442f00',fontSize:13,borderBottom:'1px solid #ddc77a'}}>
      <strong>Sender Suite — development migration.</strong> Not production ready. {runtime.smsOutboxEnabled?'Experimental SMS queue enabled by this host.':'SMS sending is disabled.'} Some workflows are not yet implemented.
    </div>
    <HashRouter><App/><Toaster position="top-right"/></HashRouter>
  </React.StrictMode>);
} catch(error) {
  root.render(<main style={{padding:32}}><h1>WordPress application configuration needed</h1><p>{error instanceof Error?error.message:'Native runtime unavailable.'}</p></main>);
}
