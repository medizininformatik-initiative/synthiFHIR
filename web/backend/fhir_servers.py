"""Shared FHIR connections. Secrets stay in a private backend file, never in job snapshots."""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import ssl
import time
from urllib.parse import urlencode, urlsplit
import urllib.error
import urllib.request
import uuid

import store

LOCAL = {
    'blaze': {'name': 'Blaze', 'url': 'http://blaze:8080/fhir', 'address': 'http://localhost:5190/fhir', 'concurrency': 1},
    'hapi': {'name': 'HAPI', 'url': 'http://hapi:8080/fhir', 'address': 'http://localhost:5191/fhir', 'concurrency': 1},
}
FIELDS = {'name', 'address', 'auth', 'username', 'password', 'token', 'flow', 'issuer',
          'tokenUrl', 'authorizationUrl', 'clientId', 'clientSecret', 'scope'}
SECRET_FIELDS = {'password', 'token', 'clientSecret'}


class ConnectionError(ValueError):
    """Safe, stable error code translated by the interface; no remote response or credentials."""


@contextmanager
def vault():
    directory = store.ROOT / 'fhir-connections'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    with (directory / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / 'connections.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            temporary = directory / (str(uuid.uuid4()) + '.tmp')
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as output:
                    json.dump(data, output)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)


def validate_url(value, authenticated=False):
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.query or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError()
        if authenticated and parsed.scheme != 'https' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ConnectionError('https_required')
    except ConnectionError:
        raise
    except ValueError:
        raise ConnectionError('invalid_url') from None
    return value.rstrip('/')


def public(profile):
    result = {k: v for k, v in profile.items() if k in FIELDS - SECRET_FIELDS or k in {'id', 'revision', 'status'}}
    result.update(external=True, url=profile['address'], concurrency=1,
                  hasPassword=bool(profile.get('password')), hasToken=bool(profile.get('token')),
                  hasClientSecret=bool(profile.get('clientSecret')))
    result['available'] = profile.get('status', {}).get('upload', False)
    return result


def get(identifier):
    if identifier in LOCAL:
        return {'id': identifier, **LOCAL[identifier], 'auth': 'none', 'external': False}
    with vault() as data:
        if identifier not in data:
            raise ConnectionError('server_missing')
        return public(data[identifier])


def active(identifier):
    with store.connect() as db:
        return any(json.loads(row['descriptor'])['target']['id'] == identifier for row in db.execute(
            "SELECT descriptor FROM uploads WHERE state IN ('queued','preparing','uploading')"))


def save(values, identifier=None):
    if set(values) - FIELDS or any(not isinstance(v, str) or len(v) > 8192 for v in values.values()):
        raise ConnectionError('invalid_fields')
    if identifier in LOCAL:
        raise ConnectionError('local_readonly')
    updating = identifier is not None
    identifier = identifier or str(uuid.uuid4())
    with vault() as data:
        if updating and identifier not in data:
            raise ConnectionError('server_missing')
        previous = data.get(identifier, {})
        if active(identifier):
            raise ConnectionError('server_busy')
        profile = {k: v.strip() if k not in SECRET_FIELDS else v for k, v in values.items()}
        identity = ['address', 'auth']
        if profile.get('auth') == 'basic':
            identity += ['username']
        elif profile.get('auth') == 'oauth2':
            identity += ['flow', 'clientId', 'tokenUrl', 'authorizationUrl', 'issuer']
        same_identity = all(previous.get(key, '') == profile.get(key, '') for key in identity)
        for key in SECRET_FIELDS:
            if not profile.get(key) and same_identity:
                profile[key] = previous.get(key, '')
        if not profile.get('name') or len(profile['name']) > 120 or profile.get('auth') not in {'none', 'basic', 'bearer', 'oauth2'}:
            raise ConnectionError('invalid_fields')
        profile['address'] = validate_url(profile.get('address', ''), profile['auth'] != 'none')
        allowed = {'name', 'address', 'auth'}
        required = []
        if profile['auth'] == 'basic':
            allowed |= {'username', 'password'}
            required = ['username', 'password']
            if ':' in profile.get('username', ''):
                raise ConnectionError('invalid_fields')
        elif profile['auth'] == 'bearer':
            allowed.add('token')
            required = ['token']
            if re.search(r'\s', profile.get('token', '')):
                raise ConnectionError('invalid_token')
        elif profile['auth'] == 'oauth2':
            allowed |= {'flow', 'issuer', 'tokenUrl', 'authorizationUrl', 'clientId', 'clientSecret', 'scope'}
            if profile.get('flow') not in {'client_credentials', 'authorization_code'}:
                raise ConnectionError('invalid_fields')
            required = ['tokenUrl', 'clientId']
            required += ['clientSecret'] if profile['flow'] == 'client_credentials' else ['authorizationUrl']
            for key in ('tokenUrl', 'authorizationUrl', 'issuer'):
                if profile.get(key):
                    profile[key] = validate_url(profile[key], True)
        if any(not profile.get(key) for key in required):
            raise ConnectionError('missing_fields')
        profile = {k: v for k, v in profile.items() if k in allowed}
        profile.update(id=identifier, revision=previous.get('revision', 0) + 1)
        data[identifier] = profile
        return public(profile)


def delete(identifier):
    if identifier in LOCAL:
        raise ConnectionError('local_readonly')
    with vault() as data:
        if active(identifier):
            raise ConnectionError('server_busy')
        if identifier not in data:
            raise ConnectionError('server_missing')
        del data[identifier]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_json(url, headers=None, body=None, timeout=20):
    request = urllib.request.Request(url, data=body, headers=headers or {'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ConnectionError('response_too_large')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ConnectionError('invalid_response')
            return result
    except urllib.error.HTTPError as error:
        error.close()
        code = 'login_rejected' if error.code == 401 else 'forbidden' if error.code == 403 else 'not_found' if error.code == 404 else 'redirect_blocked' if 300 <= error.code < 400 else 'server_error'
        raise ConnectionError(code) from None
    except urllib.error.URLError as error:
        raise ConnectionError('certificate_error' if isinstance(error.reason, ssl.SSLCertVerificationError) else 'unreachable') from None
    except (TimeoutError, OSError):
        raise ConnectionError('unreachable') from None
    except (ValueError, UnicodeError) as error:
        if isinstance(error, ConnectionError):
            raise
        raise ConnectionError('invalid_response') from None


def discover(issuer):
    issuer = validate_url(issuer, True)
    metadata = http_json(issuer + '/.well-known/openid-configuration')
    if metadata.get('issuer', '').rstrip('/') != issuer:
        raise ConnectionError('invalid_response')
    return {'issuer': issuer, 'tokenUrl': validate_url(metadata.get('token_endpoint', ''), True),
            'authorizationUrl': validate_url(metadata.get('authorization_endpoint', ''), True)}


def exchange(profile, parameters):
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
    parameters = {**parameters, 'client_id': profile['clientId']}
    if profile.get('clientSecret'):
        # RFC 6749: application/x-www-form-urlencoded client credentials in HTTP Basic.
        from urllib.parse import quote_plus
        credentials = quote_plus(profile['clientId']) + ':' + quote_plus(profile['clientSecret'])
        headers['Authorization'] = 'Basic ' + base64.b64encode(credentials.encode()).decode()
    try:
        result = http_json(profile['tokenUrl'], headers, urlencode(parameters).encode())
        token = result['access_token']
        if not isinstance(token, str) or not token or re.search(r'\s', token) or result.get('token_type', '').lower() != 'bearer':
            raise ValueError()
        duration = float(result.get('expires_in', 300))
        if not 0 < duration < 365 * 86400:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ConnectionError('oauth_failed') from None
    profile['_access'] = token
    profile['_expires'] = time.time() + duration
    if parameters.get('grant_type') == 'authorization_code':
        profile.pop('_refresh', None)
    if result.get('refresh_token') and profile['flow'] == 'authorization_code':
        if not isinstance(result['refresh_token'], str):
            raise ConnectionError('oauth_failed')
        profile['_refresh'] = result['refresh_token']
    return token


def authorization(identifier, expected=None):
    if identifier in LOCAL:
        return {}
    with vault() as data:
        if identifier not in data:
            raise ConnectionError('server_missing')
        profile = data[identifier]
        if expected and (profile['address'] != expected['url'] or profile['revision'] != expected.get('revision')):
            raise ConnectionError('server_changed')
        if profile['auth'] == 'none':
            return {}
        if profile['auth'] == 'basic':
            return {'Authorization': 'Basic ' + base64.b64encode((profile['username'] + ':' + profile['password']).encode()).decode()}
        if profile['auth'] == 'bearer':
            token = profile['token']
        elif profile.get('_access') and profile.get('_expires', 0) > time.time() + 30:
            token = profile['_access']
        elif profile['flow'] == 'client_credentials':
            token = exchange(profile, {'grant_type': 'client_credentials', 'scope': profile.get('scope', '')})
        elif profile.get('_refresh'):
            token = exchange(profile, {'grant_type': 'refresh_token', 'refresh_token': profile['_refresh']})
        else:
            raise ConnectionError('login_required')
        return {'Authorization': 'Bearer ' + token}


def begin_login(identifier, redirect_uri):
    with vault() as data:
        profile = data.get(identifier)
        if not profile or profile.get('flow') != 'authorization_code':
            raise ConnectionError('invalid_fields')
        state, verifier, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        profile['_pending'] = {'state': state, 'verifier': verifier, 'binding': binding, 'redirect': redirect_uri, 'expires': time.time() + 600}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        query = urlencode({'response_type': 'code', 'client_id': profile['clientId'], 'redirect_uri': redirect_uri,
                           'scope': profile.get('scope', ''), 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
        return profile['authorizationUrl'] + '?' + query, binding


def complete_login(state, code, binding, issuer=None, expected_id=None):
    # Consume the one-time state even when the exchange fails.
    found = None
    with vault() as data:
        for profile in data.values():
            pending = profile.get('_pending', {})
            if state and secrets.compare_digest(pending.get('state', ''), state) and (not expected_id or profile['id'] == expected_id):
                if not binding or not secrets.compare_digest(pending['binding'], binding) or pending['expires'] < time.time():
                    raise ConnectionError('login_expired')
                found = (profile['id'], profile['revision'], dict(profile), profile.pop('_pending'))
                break
    if not found:
        raise ConnectionError('login_expired')
    identifier, revision, profile, pending = found
    if issuer and profile.get('issuer') and issuer.rstrip('/') != profile['issuer'].rstrip('/'):
        raise ConnectionError('login_rejected')
    if not code:
        raise ConnectionError('login_rejected')
    exchange(profile, {'grant_type': 'authorization_code', 'code': code, 'code_verifier': pending['verifier'], 'redirect_uri': pending['redirect']})
    with vault() as data:
        if identifier not in data or data[identifier]['revision'] != revision:
            raise ConnectionError('login_expired')
        data[identifier].pop('_refresh', None)
        for key in ('_access', '_expires', '_refresh'):
            if key in profile:
                data[identifier][key] = profile[key]
    return identifier


def request(identifier, path='', query='', body=None, expected=None):
    target = get(identifier)
    if expected and (target['url'] != expected['url'] or target.get('revision') != expected.get('revision')):
        raise ConnectionError('server_changed')
    if path and not re.fullmatch(r'[A-Z][A-Za-z0-9]*(?:/[A-Za-z0-9.-]{1,64})?|metadata', path):
        raise ConnectionError('invalid_query')
    if '#' in query or len(query) > 8192:
        raise ConnectionError('invalid_query')
    url = target['url'] + ('/' + path if path else '') + ('?' + query if query else '')
    return http_json(url, {'Accept': 'application/fhir+json', 'Content-Type': 'application/fhir+json', **authorization(identifier, target)}, body, 120 if body else 2 if identifier in LOCAL else 20)


def check(identifier):
    target = get(identifier)
    status = {'code': 'unreachable', 'read': False, 'upload': False}
    try:
        metadata = request(identifier, 'metadata')
        if metadata.get('resourceType') != 'CapabilityStatement':
            raise ConnectionError('invalid_response')
        if not str(metadata.get('fhirVersion', '')).startswith('4.0.'):
            raise ConnectionError('unsupported_version')
        rest = [part for part in metadata.get('rest', []) if part.get('mode') == 'server']
        resource_types = sorted({r['type'] for part in rest for r in part.get('resource', [])
                                 if any(i.get('code') in {'read', 'search-type'} for i in r.get('interaction', []))})
        upload = any(i.get('code') == 'transaction' for part in rest for i in part.get('interaction', []))
        status.update(code='ready' if upload else 'read_only', read=bool(resource_types), upload=upload, resourceTypes=resource_types)
    except ConnectionError as error:
        status['code'] = str(error)
    except (TypeError, ValueError, KeyError):
        status['code'] = 'invalid_response'
    status['checked'] = time.time()
    if target['external']:
        with vault() as data:
            if identifier in data and data[identifier]['revision'] == target['revision']:
                data[identifier]['status'] = status
    return {**target, 'status': status, 'available': status['upload']}


def targets():
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        local = list(executor.map(check, LOCAL))
    with vault() as data:
        return local + [public(profile) for profile in data.values()]
