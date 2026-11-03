"""Verify both pages of a two-patient search from the caller's network."""
import json
import sys
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

base = sys.argv[1]
patients = {'pagination-a', 'pagination-b'}
headers = {'Accept': 'application/fhir+json'}
if sys.argv[2:] == ['--seed']:
    for identifier in sorted(patients):
        body = json.dumps({'resourceType': 'Patient', 'id': identifier}).encode()
        request = Request(f'{base}/Patient/{identifier}', data=body, method='PUT',
                          headers={**headers, 'Content-Type': 'application/fhir+json'})
        with urlopen(request, timeout=30):
            pass

query = '_id=pagination-a,pagination-b&_count=1'
url = f'{base}/Patient?{query}'
seen = set()
for page in (1, 2):
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        bundle = json.load(response)
    links = {link['relation']: link['url'] for link in bundle['link']}
    assert all(link.startswith(f'{base}/Patient') for link in links.values()), links
    if page == 1:
        self_url = urlsplit(links['self'])
        assert self_url._replace(query='').geturl() == f'{base}/Patient', links
        assert parse_qs(self_url.query) == parse_qs(query), links
    entry, = bundle['entry']
    patient = entry['resource']
    assert patient['resourceType'] == 'Patient' and patient['id'] in patients, patient
    assert entry['fullUrl'] == f"{base}/Patient/{patient['id']}", entry
    seen.add(patient['id'])
    if page == 1:
        url = links['next']
        assert url.startswith(f'{base}/Patient/'), url
    else:
        assert 'next' not in links, links
assert seen == patients, seen
print(f'{base}: search self verified, followed next, verified both patient fullUrls')
