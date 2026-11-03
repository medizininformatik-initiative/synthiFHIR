"""Check the Patient Swagger page after a Docker client has fetched metadata."""
import re
from urllib.parse import urljoin
from urllib.request import urlopen

page = 'http://localhost:5191/fhir/swagger-ui/?page=Patient'
with urlopen(page, timeout=30) as response:
    html = response.read().decode()
definition = re.search(r'^\s*url:\s*"([^"]+)"', html, re.MULTILINE)
assert definition and definition[1] == '/fhir/api-docs?page=Patient', 'Expected a same-origin OpenAPI URL'
with urlopen(urljoin(page, definition[1]), timeout=30) as response:
    spec = response.read().decode()
assert re.search(r'^\s*- url: /fhir$', spec, re.MULTILINE), 'Expected a relative OpenAPI server URL'
assert re.search(r'^  /Patient:\s*\n    get:', spec, re.MULTILINE), 'Missing Patient search operation'
