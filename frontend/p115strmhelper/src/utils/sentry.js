import * as Sentry from '@sentry/vue';

const FRONTEND_DSN = 'https://9a6338e4ec0a4e41bd24ad5b84eb437b@glitchtip.ddsrem.com/7';

export function initSentry(app = null, router = null, options = {}) {
  const isEnabled = options.enabled !== false;

  if (!isEnabled) {
    return;
  }

  const DSN = FRONTEND_DSN;

  try {
    const initOptions = {
      dsn: DSN,
      environment: process.env.NODE_ENV || 'production',
      ...(app ? { app, router } : {}),
      integrations: [
        Sentry.replayIntegration({
          maskAllText: true,
          blockAllMedia: true,
        }),
      ],

      tracesSampleRate: 0,
      replaysSessionSampleRate: 0,
      replaysOnErrorSampleRate: 1.0,

      ignoreErrors: [
        'top.GLOBALS',
        'originalCreateNotification',
        'canvas.contentDocument',
        'MyApp_RemoveAllHighlights',
        'atomicFindClose',
        'fb_xd_fragment',
        'bmi_SafeAddOnload',
        'EBCallBackMessageReceived',
        'conduitPage',
        'NetworkError',
        'Failed to fetch',
        'Network request failed',
        'Loading chunk',
        'ChunkLoadError',
        'Loading CSS chunk',
        'ResizeObserver loop',
        'ResizeObserver loop limit exceeded',
      ],

      denyUrls: [
        /extensions\//i,
        /^chrome:\/\//i,
        /^chrome-extension:\/\//i,
        /^https?:\/\/localhost/i,
      ],


      beforeSend(event, hint) {
        const url = event.request?.url || hint.originalException?.stack || '';
        const isPluginError =
          url.includes('p115strmhelper') ||
          url.includes('115strmhelper');

        if (!isPluginError && url && !url.includes('p115strmhelper') && !url.includes('115strmhelper')) {
          if (url.includes('/plugins/') && !url.includes('p115strmhelper')) {
            return null;
          }
        }

        if (hint && hint.originalException) {
          const error = hint.originalException;

          if (
            error.message &&
            (
              error.message.includes('ResizeObserver loop') ||
              error.message.includes('Non-Error promise rejection') ||
              error.message.includes('NetworkError') ||
              error.message.includes('Failed to fetch') ||
              error.message.includes('Loading chunk') ||
              error.message.includes('ChunkLoadError')
            )
          ) {
            return null;
          }

          if (error.name === 'Vue warn' || error.message.includes('[Vue warn]')) {
            return null;
          }
        }

        return event;
      },
    };

    Sentry.init(initOptions);

    Sentry.setUser({
      id: undefined,
    });
  } catch (error) {
    console.error('[Sentry] Failed to initialize:', error);
  }
}

export function captureError(error, context = {}) {
  if (context) {
    Sentry.withScope((scope) => {
      if (context.tags) {
        Object.keys(context.tags).forEach((key) => {
          scope.setTag(key, context.tags[key]);
        });
      }
      if (context.extra) {
        Object.keys(context.extra).forEach((key) => {
          scope.setExtra(key, context.extra[key]);
        });
      }
      Sentry.captureException(error);
    });
  } else {
    Sentry.captureException(error);
  }
}

export function captureMessage(message, level = 'info') {
  Sentry.captureMessage(message, level);
}

export function setUser(user) {
  Sentry.setUser(user);
}

export function setContext(key, context) {
  Sentry.setContext(key, context);
}

export function setTag(key, value) {
  Sentry.setTag(key, value);
}
