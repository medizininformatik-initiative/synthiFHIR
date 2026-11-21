# Verify a TORCH extraction

This example uses the TORCH service supplied by the `data-portal` Compose profile
and the shared Blaze server. Run it from the project root with Python 3 and Docker
Compose available. It starts only Blaze, its proxy, TORCH and the TORCH file server.
The Data Portal UI, Keycloak and Elasticsearch are not needed for this direct API
check. See the [deployment resource budgets](../deployment/README.md#resource-sizing)
when running it alongside other services.

## Run the example

The following block starts the services, waits for readiness and executes both
extraction checks. Set `EVIDENCE_DIR` to a new directory outside the repository if
you want to retain results at a particular location. An existing directory is
rejected to protect earlier evidence.

```bash
(
set -eu
EVIDENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/synthifhir-torch.XXXXXX")/evidence"
COMPOSE_FILE=web/compose.yml
trap 'status=$?; if [ "$status" -ne 0 ]; then echo "TORCH check failed. Evidence: $EVIDENCE_DIR" >&2; docker compose -f "$COMPOSE_FILE" logs --tail 80 blaze torch torch-nginx >&2 || true; fi' EXIT

docker compose -f "$COMPOSE_FILE" --profile data-portal up -d blaze blaze-proxy torch torch-nginx
python3 - <<'PY'
import time
import urllib.error
import urllib.request

for url in ('http://localhost:5190/fhir/metadata', 'http://localhost:5193/actuator/health'):
    deadline = time.monotonic() + 180
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise SystemExit(f'Service did not become ready: {url}')
        time.sleep(2)
print('Blaze and TORCH are ready.')
PY
python3 web/integration/smoke_torch_profile.py "$EVIDENCE_DIR"
echo "Verification and downloaded results: $EVIDENCE_DIR"
)
```

Success prints `PASS` and the evidence directory. The script removes its input
resources after the checks, including on extraction or verification failure.
Services stay running and TORCH job/output files remain in `torch-data`.
Use the [Compose lifecycle instructions](../deployment/README.md#stopping-services)
to stop services while retaining data. If the process is forcibly terminated,
`input.json` identifies the exact fixture resources for targeted cleanup; do not
reset the shared Blaze store to clean up this example.

## Inputs and expected results

The script creates three synthetic Patients, each with one Condition carrying
ICD-10-GM `E11.9` and a fixed recording timestamp. Every run uses fresh UUID-based
IDs. It explicitly selects two patient IDs; the third is an unselected control.
Existing uploads stay in Blaze and are outside the explicit selection.

The first extraction uses the pinned upstream
[`example-crtdl.json`](../deployment/upstream/data-node/torch/queries/example-crtdl.json).
The request passes the patient IDs as FHIR Parameters alongside the encoded CRTDL.
Those IDs determine the cohort for this example; the gender criteria embedded in
the CRTDL are not a tested cohort query.

The extraction requests `Condition.code` and `Condition.recordedDate`. TORCH
also retains resource IDs, profile metadata and the patient reference needed to
connect the diagnoses to their patients. The Patient group requests no optional
fields. The checks verify:

- Exactly the two selected Patients and their two Conditions are returned.
- Diagnosis codes, recording timestamps and patient references match the input.
- The unrequested Condition note, Patient gender and birth date are absent.
- The control patient and its diagnosis are absent.
- Additional TORCH Provenance resources are allowed.

These are small, hand-built synthetic extraction fixtures. Declaring the MII
profiles lets TORCH select the resources; it is not a claim that these fixtures
pass complete KDS profile validation. This check exercises extraction, not the
SynthiFHIR generation pipeline or the FHIR validator.

## Consent and service prerequisites

The first CRTDL contains no `Einwilligung` criteria. TORCH 1.0.1 therefore skips
consent enforcement, and the selected synthetic patients are extracted without
Consent resources. The example does not infer permission from a missing Consent.

The second extraction adds both MII consent provision codes `.6` and `.8`
(`2.16.840.1.113883.3.1937.777.24.5.3.6` and
`2.16.840.1.113883.3.1937.777.24.5.3.8`) in `Einwilligung` criteria. For the same
patients without Consent records, it verifies **zero extracted resources and two
`CONSENT_FETCH` patient exclusions**. A completed job with no output is the
expected result here. The downloaded exclusion reports explain the outcome.

This pair checks consent activation and missing-consent exclusion. It does not
cover permitted consent periods, revocation, retrospective provisions or all
consent combinations. See the
[versioned TORCH consent documentation](https://github.com/medizininformatik-initiative/torch/blob/v1.0.1/docs/implementation/consent.md)
for those rules.

The Compose deployment configures TORCH's CQL path against Blaze. Explicit patient
IDs avoid a cohort query in this example, so neither FLARE nor the portal is
required. TORCH uses its bundled profiles/mappings; no external terminology server
is needed for these exact codes and fields. This does not verify arbitrary
terminology expansion, CQL cohort selection, portal authentication or the central
FDPG/DSF workflow. The diagnostic endpoints are bound to loopback and use the
local deployment's HTTP access.

## Evidence and troubleshooting

`target.json` records the host endpoints and selected IDs. `input.json` and
`import-result.json` record the fixtures and import. Each extraction directory
contains the effective `crtdl.json`, `parameters.json`, `status-url.txt`, final
`result.json`, downloaded NDJSON where applicable, and TORCH's summary and
exclusion reports. `verification.json` records passed assertions;
`cleanup-result.json` confirms fixture deletion.

Submission must return HTTP 202. The script follows the local status URL until
HTTP 200, requires a completed job, downloads local output URLs and checks their
content. HTTP errors are printed; failed jobs and unexpected results cause a
nonzero exit. The executable block prints Compose logs on failure. Inspect the
saved result's job issues and downloaded exclusion reports for extraction-level
explanations. The script rejects unexpected status/output hosts.
