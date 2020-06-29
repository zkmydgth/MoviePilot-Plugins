import { getCurrentInstance, createApp, h } from 'vue';
import { initSentry } from './sentry.js';

let sentryInitialized = false;
let tempApp = null;

export function ensureSentryInitialized() {
  if (sentryInitialized) {
    return;
  }

  try {
    let app = null;
    try {
      const instance = getCurrentInstance();
      if (instance) {
        if (instance.appContext && instance.appContext.app) {
          app = instance.appContext.app;
        } else if (instance.app) {
          app = instance.app;
        }
      }
    } catch (e) {
      // Ignore
    }

    if (!app && typeof window !== 'undefined') {
      if (window.__VUE_APP__) {
        app = window.__VUE_APP__;
      } else if (window.__VUE_INSTANCE__) {
        app = window.__VUE_INSTANCE__;
      }
    }

    if (!app) {
      try {
        tempApp = createApp({
          render: () => h('div')
        });
        app = tempApp;
      } catch (e) {
        console.error('[Sentry] Failed to create temporary Vue app:', e);
      }
    }

    initSentry(app, null, {
      enabled: true,
    });
    
    sentryInitialized = true;
  } catch (error) {
    console.error('[Sentry] Failed to initialize:', error);
  }
}
