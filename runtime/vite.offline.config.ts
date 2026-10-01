// Copy beside qa/verification/vite.config.ts in the frontend checkout.
// Shared-dev API proxy only; browser market/vendor requests are blocked by CSP.
import { defineConfig } from 'vite';
import verificationConfig from './vite.config.ts';

export default defineConfig(async (env) => {
  const config = await verificationConfig(env);
  const proxy = config.server?.proxy ?? {};
  return {
    ...config,
    plugins: [
      ...(config.plugins ?? []),
      {
        name: 'chart-shared-dev-offline',
        configureServer(server) {
          server.middlewares.use((req, res, next) => {
            if (/^\/(?:taas|chart\/tsdb|cme-snapshot)(?:\/|$)/.test(req.url ?? '')) {
              res.statusCode = 503;
              res.setHeader('Content-Type', 'application/json');
              res.end(JSON.stringify({ error: 'This integration is disabled in offline shared development' }));
              return;
            }
            next();
          });
        },
      },
    ],
    server: {
      ...config.server,
      headers: {
        ...config.server?.headers,
        'Content-Security-Policy': "default-src 'self' blob: data:; connect-src 'self' ws://localhost:* ws://127.0.0.1:*; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; frame-src 'none'; form-action 'self'; base-uri 'self'",
      },
      // Drop upstream market-data proxies from the ordinary development config.
      proxy: Object.fromEntries(['/api/v1', '/api/v2', '/api'].filter(key => proxy[key]).map(key => [key, proxy[key]])),
    },
  };
});
