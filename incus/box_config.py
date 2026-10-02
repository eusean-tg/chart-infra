"""Generate independent, offline application identities using retained backing credentials."""
import json
import os
import secrets
import time
from pathlib import Path
import backing as b

SERVICES = {'auth': ('auth-service-backend', 4001, '/api/v2/health'),
            'tharamine': ('tharamine-user-service', 5001, '/api/v2/health'),
            'orange': ('orange-v2-backend', 3000, '/api/v1/health/services')}


def prepare(state):
    receipt = state / 'identity.json'
    if receipt.exists():
        verify(state)
        print('Retained application identities verified.')
        return
    b.require(not any((state / s).exists() for s in SERVICES), 'Unregistered app identity exists; inspect interrupted preparation')
    backing = b.read(b.STATE / 'credentials.json')
    keys = {}
    for name, ec in [('issuer', False), ('backend', False), ('orange', False), ('tharamine', True)]:
        args = ['openssl', 'genpkey', '-algorithm', 'EC' if ec else 'RSA', '-pkeyopt',
                'ec_paramgen_curve:P-256' if ec else 'rsa_keygen_bits:2048']
        private = b.run(args)
        public = b.run(['openssl', 'pkey', '-pubout'], stdin=private)
        keys[name] = (private + '\n', public + '\n')
    created = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    jwt = [{'kid': 'local-key-1', 'kms_key_id': None, 'created_at': created, 'rotated_at': None,
            'sign_algo': 'RSASSA_PKCS1_V1_5_SHA_256', 'public_key': keys['issuer'][1]}]
    registry = [{'service': name, 'keys': [{'kid': 'chart-dev-1', 'created_at': created,
                  'sign_algo': 'ES256' if s == 'tharamine' else 'RS256', 'public_key': keys[s][1]}]}
                for s, name in [('orange', 'orange_v2_backend'), ('tharamine', 'tharamine-user-service')]]
    backend_key = secrets.token_hex(32)
    for service, (_, port, _) in SERVICES.items():
        path = state / service
        path.mkdir(mode=0o700)
        os.chown(path, 1000, 1000)
        c = backing['mongo'][service]
        cfg = {'NODE_ENV': 'development', 'PORT': str(port), 'MONGO_DB_NAME': c['db'],
               'MONGO_URI': f'mongodb://{c["user"]}:{c["password"]}@mongo:27017/{c["db"]}?authSource=admin&replicaSet=rs0',
               'REDIS_URL': 'redis://:' + backing['redis_password'] + '@redis:6379',
               'OTEL_SDK_DISABLED': 'true', 'OTEL_TRACES_EXPORTER': 'none', 'OTEL_METRICS_EXPORTER': 'none',
               'OTEL_LOGS_EXPORTER': 'none', 'SENTRY_ENABLED': 'false', 'METRICS_ENABLED': 'false',
               'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_REGION': 'us-east-1', 'AWS_ACCESS_KEY_ID': 'offline-local',
               'AWS_SECRET_ACCESS_KEY': 'offline-local', 'AWS_ENDPOINT_URL': 'http://127.0.0.1:9',
               'AWS_ENDPOINT_URL_SES': 'http://127.0.0.1:9', 'AWS_MAX_ATTEMPTS': '1',
               'JWT_DEFAULT_KEY': 'local-key-1', 'JWT_KEYS': json.dumps(jwt), 'API_BACKEND_KEY': backend_key,
               'AUTH_SERVICE_URL': 'http://auth:4001', 'USER_SERVICE_URL': 'http://tharamine:5001',
               'FRONTEND_URL': 'http://localhost:8080/chart', 'FRONTEND_EXTERNAL_DOMAIN': 'http://localhost:8080/chart',
               'CORS_ORIGINS': 'http://localhost:8080,http://127.0.0.1:8080,http://localhost:8097,http://127.0.0.1:8097',
               'COOKIE_DOMAIN': '', 'THARAMINE_COOKIE_DOMAIN': '', 'OPENMARKET_COOKIE_DOMAIN': '',
               'EMAIL_PROVIDER': 'ses', 'TWOFA_ENABLED': 'false'}
        if service == 'auth':
            for name, value in [('private.pem', keys['issuer'][0]), ('public.pem', keys['issuer'][1]),
                                ('backend-private.pem', keys['backend'][0]), ('backend-public.pem', keys['backend'][1])]:
                b.write(path / name, value, uid=1000)
            cfg.update(PRIVATE_KEY_PATH='/run/chart/private.pem', PUBLIC_KEY_PATH='/run/chart/public.pem',
                       PRIVATE_KEY_PATH_BACKEND='/run/chart/backend-private.pem', JWT_PRIVATE_KEY=keys['issuer'][0],
                       AWS_KMS_KEYS=json.dumps(jwt), ALGORITHM='RS256', SERVICE_AUTH_KEYS=json.dumps(registry),
                       SESSION_SECRET=secrets.token_hex(32), TWOFA_ENCRYPT_KEY=secrets.token_hex(32))
        else:
            b.write(path / 'private.pem', keys[service][0], uid=1000)
            b.write(path / 'public.pem', keys[service][1], uid=1000)
            cfg['INTERNAL_SERVICE_KEY'] = backend_key
            if service == 'orange':
                cfg.update(SERVICE_AUTH_NAME='orange_v2_backend', SERVICE_AUTH_KID='chart-dev-1',
                           SERVICE_SIGN_ALGO='RS256', SERVICE_AUTH_KEY=keys[service][0],
                           MARKETPLACE_OUTBOX_RUNNER_ENABLED='false', MARKET_EVENTS_INGEST_ENABLED='false',
                           KSCRIPT_ALERT_ENTITLEMENT_RECONCILE_MS='0', ENTITLEMENT_SERVICE_URL='http://127.0.0.1:9')
            else:
                cfg.update(SERVICE_AUTH_PRIVATE_KEY=keys[service][0], SERVICE_AUTH_KID='chart-dev-1',
                           EMAIL_FOLLOWER_FANOUT_ENABLED='false', OM_ROOM_NOTIFY_ENABLED='false',
                           OM_ROOM_UNFURL_ENABLED='false', VOICE_ENABLED='false', ROOMS_CANVAS_CHECKPOINT_V1_STORE='false',
                           KSCRIPT_ALERT_RECONCILE_MS='0')
        b.write(path / 'config.json', json.dumps(cfg, indent=2) + '\n', uid=1000)
        launcher = Path(__file__).parent / 'launch.cjs'
        if not launcher.exists():
            launcher = Path(__file__).resolve().parents[1] / 'runtime/launch.cjs'
        b.write(path / 'launch.cjs', launcher.read_text(), uid=1000)
    b.save(receipt, {'backing_credentials_sha256': b.digest(b.STATE / 'credentials.json'),
                    'files': {str(p.relative_to(state)): b.digest(p) for s in SERVICES for p in (state / s).iterdir()}})
    print('Independent app keys/config prepared; existing Mongo/cache identities reused.')


def verify(state):
    inv = b.read(state / 'identity.json')
    b.require(inv['backing_credentials_sha256'] == b.digest(b.STATE / 'credentials.json'), 'Backing credentials changed')
    for rel, expected in inv['files'].items():
        p = b.safe(state / rel)
        b.require(b.digest(p) == expected, f'App identity/config changed without reviewed generation: {rel}')
        b.require(p.stat().st_uid == 1000 and p.stat().st_mode & 0o077 == 0, f'Private runtime file permissions differ: {rel}')
    return inv
