"""Exercise real HTTP authentication, token renewal, uploads and reads against a local fake provider."""
import base64
import json
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from api import app, OAuthAccessLogFilter
import bulk_uploads
import fhir_servers as servers
import store
from test_queue import QueueFixture


class ServerTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.expected = None
        self.tokens = 0
        self.verifier = None
        self.transaction = True
        self.fhir_version = '4.0.1'
        self.fail = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, data):
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_GET(self):
                outer.calls.append((self.path, self.headers.get('Authorization')))
                if self.path == '/.well-known/openid-configuration':
                    self.respond(200, {'issuer': outer.url, 'token_endpoint': outer.url + '/token', 'authorization_endpoint': outer.url + '/authorize'})
                    return
                if self.path == '/redirect/metadata':
                    self.send_response(302)
                    self.send_header('Location', outer.url + '/stolen')
                    self.end_headers()
                    return
                if self.headers.get('Authorization') != outer.expected:
                    self.respond(401, {'secret': self.headers.get('Authorization')})
                    return
                if outer.fail:
                    self.respond(outer.fail, {'secret': self.headers.get('Authorization')})
                    return
                if self.path.endswith('/metadata'):
                    self.respond(200, {'resourceType': 'CapabilityStatement', 'fhirVersion': outer.fhir_version, 'rest': [
                        {'mode': 'server', 'interaction': [{'code': 'transaction'}] if outer.transaction else [],
                         'resource': [{'type': 'Patient', 'interaction': [{'code': 'read'}, {'code': 'search-type'}]}]}]})
                else:
                    self.respond(200, {'resourceType': 'Bundle', 'type': 'searchset', 'entry': [{'resource': {'resourceType': 'Patient', 'id': 'p1'}}]})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                outer.calls.append((self.path, self.headers.get('Authorization')))
                if self.path == '/token':
                    parameters = parse_qs(raw.decode())
                    grant = parameters.get('grant_type', [''])[0]
                    if grant == 'authorization_code':
                        assert parameters.get('code_verifier') and parameters.get('redirect_uri')
                        outer.verifier = parameters['code_verifier'][0]
                    elif grant == 'refresh_token':
                        assert parameters.get('refresh_token') == ['refresh-private']
                    else:
                        assert self.headers.get('Authorization') == 'Basic ' + base64.b64encode(b'app:secret-private').decode()
                    outer.tokens += 1
                    self.respond(200, {'access_token': 'access-private', 'token_type': 'Bearer', 'expires_in': 120, 'refresh_token': 'refresh-private'})
                    return
                if self.headers.get('Authorization') != outer.expected:
                    self.respond(401, {'secret': self.headers.get('Authorization')})
                    return
                bundle = json.loads(raw)
                self.respond(200, {'resourceType': 'Bundle', 'type': 'transaction-response', 'entry': [{'response': {'status': '200 OK'}} for _ in bundle['entry']]})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def profile(self, auth='none', **fields):
        return servers.save({'name': 'External test', 'address': self.url + '/fhir', 'auth': auth, **fields})

    def assert_upload_and_read(self, profile):
        status = servers.check(profile['id'])
        self.assertTrue(status['status']['read'])
        self.assertTrue(status['available'])
        self.assertEqual('Patient', servers.request(profile['id'], 'Patient')['entry'][0]['resource']['resourceType'])
        bundle = {'resourceType': 'Bundle', 'type': 'transaction', 'entry': [{'resource': {'resourceType': 'Patient', 'id': 'p1'}, 'request': {'method': 'PUT', 'url': 'Patient/p1'}}]}
        bulk_uploads.send(profile['url'], bundle, target=profile)

    def test_none_basic_and_bearer_for_upload_and_read(self):
        cases = [('none', {}, None), ('basic', {'username': 'person', 'password': 'password-private'}, 'Basic ' + base64.b64encode(b'person:password-private').decode()),
                 ('bearer', {'token': 'bearer-private'}, 'Bearer bearer-private')]
        for auth, values, expected in cases:
            with self.subTest(auth=auth):
                self.expected = expected
                profile = self.profile(auth, **values)
                self.assert_upload_and_read(profile)
                self.assertFalse(any(key in profile for key in servers.SECRET_FIELDS))
                with patch.object(servers, 'LOCAL', {}):
                    serialized = json.dumps(servers.targets())
                for secret in values.values():
                    if 'private' in secret:
                        self.assertNotIn(secret, serialized)
        path = store.ROOT / 'fhir-connections/connections.json'
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)

    def test_client_credentials_cache_and_refresh(self):
        self.expected = 'Bearer access-private'
        profile = self.profile('oauth2', flow='client_credentials', tokenUrl=self.url + '/token', clientId='app', clientSecret='secret-private')
        self.assert_upload_and_read(profile)
        self.assertEqual(1, self.tokens)
        with servers.vault() as data:
            data[profile['id']]['_expires'] = 0
        servers.request(profile['id'], 'Patient')
        self.assertEqual(2, self.tokens)
        self.assertNotIn('access-private', json.dumps(servers.get(profile['id'])))

    def test_browser_login_pkce_binding_one_time_callback_and_refresh(self):
        self.expected = 'Bearer access-private'
        profile = self.profile('oauth2', flow='authorization_code', tokenUrl=self.url + '/token', authorizationUrl=self.url + '/authorize', clientId='public')
        self.assertEqual('login_required', servers.check(profile['id'])['status']['code'])
        begin = self.client.post('/api/fhir-targets/' + profile['id'] + '/login')
        query = parse_qs(urlsplit(begin.json()['url']).query)
        self.assertEqual(['S256'], query['code_challenge_method'])
        self.assertTrue(query['code_challenge'])
        callback = '/api/fhir-oauth/callback/' + profile['id']
        args = {'state': query['state'][0], 'code': 'authorization-private'}
        stranger = TestClient(app).get(callback, params=args, follow_redirects=False)
        self.assertIn('login_expired', stranger.headers['location'])
        response = self.client.get(callback, params=args, follow_redirects=False)
        self.assertEqual('/?fhirLogin=success', response.headers['location'])
        expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual([expected_challenge], query['code_challenge'])
        self.assertNotIn('authorization-private', response.text)
        self.assertIn('login_expired', self.client.get(callback, params=args, follow_redirects=False).headers['location'])
        self.assert_upload_and_read(profile)
        with servers.vault() as data:
            data[profile['id']]['_expires'] = 0
        servers.request(profile['id'], 'Patient')
        self.assertEqual(2, self.tokens)

    def test_validation_redaction_and_redirect_rejection(self):
        for address in ['file:///etc/passwd', 'https://user:secret@example.org/fhir', 'https://example.org/fhir?x=1', 'https://example.org/#fragment']:
            with self.assertRaisesRegex(ValueError, 'invalid_url'):
                servers.save({'name': 'invalid', 'address': address, 'auth': 'none'})
        with self.assertRaisesRegex(ValueError, 'https_required'):
            servers.save({'name': 'invalid', 'address': 'http://example.org/fhir', 'auth': 'bearer', 'token': 'secret'})
        profile = self.profile('bearer', token='bearer-private')
        self.assertEqual('login_rejected', servers.check(profile['id'])['status']['code'])
        self.expected = 'Bearer bearer-private'
        self.fail = 403
        response = self.client.get('/api/fhir-targets/' + profile['id'] + '/resources')
        self.assertEqual('forbidden', response.json()['detail'])
        self.assertNotIn('bearer-private', response.text)
        redirect = servers.save({'name': 'redirect', 'address': self.url + '/redirect', 'auth': 'bearer', 'token': 'bearer-private'})
        self.assertEqual('redirect_blocked', servers.check(redirect['id'])['status']['code'])
        self.assertFalse(any(path == '/stolen' for path, _ in self.calls))
        for path in ['https://other.example/Patient', '../Patient', 'Patient/../secret', '//other.example']:
            with self.assertRaisesRegex(ValueError, 'invalid_query'):
                servers.request(profile['id'], path)

    def test_capabilities_discovery_and_no_writes_during_check(self):
        profile = self.profile()
        self.transaction = False
        status = servers.check(profile['id'])
        self.assertTrue(status['status']['read'])
        self.assertFalse(status['available'])
        self.assertEqual([('/fhir/metadata', None)], self.calls)
        self.fhir_version = '5.0.0'
        self.assertEqual('unsupported_version', servers.check(profile['id'])['status']['code'])
        self.assertEqual(self.url + '/token', servers.discover(self.url)['tokenUrl'])

    def test_edit_retains_secret_clear_on_auth_change_and_protects_running_upload(self):
        profile = self.profile('basic', username='person', password='password-private')
        edited = servers.save({'name': 'Renamed', 'address': self.url + '/fhir', 'auth': 'basic', 'username': 'person', 'password': ''}, profile['id'])
        self.assertTrue(edited['hasPassword'])
        with self.assertRaisesRegex(ValueError, 'missing_fields'):
            servers.save({'name': 'Other server', 'address': self.url + '/other', 'auth': 'basic', 'username': 'person', 'password': ''}, profile['id'])
        self.assertEqual(edited['address'], servers.get(profile['id'])['address'])
        with self.assertRaisesRegex(ValueError, 'server_changed'):
            servers.request(profile['id'], 'Patient', expected=profile)
        identifier = str(uuid.uuid4())
        with store.connect() as db:
            db.execute('INSERT INTO uploads(id,state,created,descriptor) VALUES (?,?,?,?)', (identifier, 'queued', time.time(), json.dumps({'target': edited})))
        for operation in [lambda: servers.delete(profile['id']), lambda: servers.save({'name': 'Changed', 'address': self.url, 'auth': 'none'}, profile['id'])]:
            with self.assertRaisesRegex(ValueError, 'server_busy'):
                operation()
        with store.connect() as db:
            db.execute("UPDATE uploads SET state='succeeded'")
        changed = servers.save({'name': 'Changed', 'address': self.url + '/fhir', 'auth': 'none'}, profile['id'])
        self.assertFalse(changed['hasPassword'])
        servers.delete(profile['id'])
        with self.assertRaisesRegex(ValueError, 'server_missing'):
            servers.get(profile['id'])

    def test_new_browser_login_does_not_reuse_previous_accounts_refresh_token(self):
        profile = self.profile('oauth2', flow='authorization_code', tokenUrl=self.url + '/token', authorizationUrl=self.url + '/authorize', clientId='public')
        with servers.vault() as data:
            data[profile['id']]['_refresh'] = 'old-account-refresh'
        url, binding = servers.begin_login(profile['id'], 'http://localhost/callback')
        state = parse_qs(urlsplit(url).query)['state'][0]
        with patch.object(servers, 'http_json', return_value={'access_token': 'new-account-access', 'token_type': 'Bearer', 'expires_in': 60}):
            servers.complete_login(state, 'code', binding, expected_id=profile['id'])
        with servers.vault() as data:
            self.assertNotIn('_refresh', data[profile['id']])

    def test_malformed_metadata_has_safe_status(self):
        profile = self.profile()
        with patch.object(servers, 'http_json', return_value={'resourceType': 'CapabilityStatement', 'fhirVersion': '4.0.1', 'rest': None}):
            self.assertEqual('invalid_response', servers.check(profile['id'])['status']['code'])

    def test_api_crud_and_cross_origin_mutation(self):
        body = {'name': 'API test', 'address': self.url + '/fhir', 'auth': 'bearer', 'token': 'bearer-private'}
        rejected = self.client.post('/api/fhir-targets', json=body, headers={'Origin': 'https://unrelated.example'})
        self.assertEqual(403, rejected.status_code)
        created = self.client.post('/api/fhir-targets', json=body)
        self.assertEqual(201, created.status_code)
        self.assertNotIn('bearer-private', created.text)
        identifier = created.json()['id']
        updated = self.client.put('/api/fhir-targets/' + identifier, json={**body, 'name': 'Updated', 'token': ''})
        self.assertEqual(200, updated.status_code)
        self.assertTrue(updated.json()['hasToken'])
        self.assertEqual('no-store', updated.headers['cache-control'])
        self.assertEqual(204, self.client.delete('/api/fhir-targets/' + identifier).status_code)

    def test_callback_access_log_keeps_path_but_omits_authorization_code(self):
        import logging
        record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                                   ('127.0.0.1', 'GET', '/api/fhir-oauth/callback/id?code=secret-code&state=secret-state', '1.1', 303), None)
        self.assertTrue(OAuthAccessLogFilter().filter(record))
        self.assertIn('/api/fhir-oauth/callback/id', record.getMessage())
        self.assertNotIn('secret-', record.getMessage())
