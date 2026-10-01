// Read only this service's retained development configuration. Never log it.
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const env = JSON.parse(fs.readFileSync('/run/chart/config.json', 'utf8'));
const child = spawn('/app/node_modules/.bin/tsx', ['watch', 'src/index.ts'], {
  cwd: '/app', env: { ...process.env, ...env }, stdio: 'inherit'
});
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
