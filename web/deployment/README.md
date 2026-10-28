# Local FHIR servers and Data Portal

Use Docker Compose 2.24.4 or newer. A **Compose profile** selects services;
a **configuration** controls dataset generation; a **FHIR profile** specifies
resource constraints.

Choose a startup option in the [main README](../../README.md#start-with-docker),
ordered from the complete Data Portal setup to generation only.

## Resource sizing

The [startup budgets](../../README.md#system-requirements) are planning
recommendations for a dedicated Docker environment and small datasets. They are
not benchmarked minimums. CPU and disk recommendations allow for building images,
initialization and retained results; no minimum CPU/disk threshold has been measured.

The workbench container memory ceilings total about 2.8 GiB, or 4.8 GiB with
Blaze. The full portal package adds the portal backend, Elasticsearch, Keycloak,
TORCH, databases and initialization services. Plan 24 GiB of Docker memory for
combined local testing and account for other running projects. These are planning
budgets, not measured minimums; container limits are ceilings rather than actual usage.

The workbench and individual FHIR services have run in an 8 GiB Docker VM.
Portal queries, TORCH extraction and evaluation have been exercised in separate
stages. A complete portal, generation and extraction running concurrently has
not been validated at a minimum memory allocation. Concurrent startup alongside
other workloads exhausted that 8 GiB VM. Do not use 8 GiB as a full-portal budget.
The portal backend uses a 2 GiB Java heap within a 3 GiB container; its PostgreSQL
container allows 1 GiB for the ontology migration.

Allocate the recommended RAM to Docker itself on Docker Desktop, with additional
host RAM for the OS and applications. Account for other containers separately.
Keep free space in Docker's disk image as well as on its host filesystem; retained
runs, build cache and database growth can exceed the starting disk budget.

## Service addresses

| Service | Local address | Persistent storage |
| --- | --- | --- |
| Workbench | http://localhost:5184 | `workbench` |
| Blaze FHIR R4 | http://localhost:5190/fhir | `blaze-data` |
| HAPI FHIR R4 | http://localhost:5191/fhir | `hapi-data` (PostgreSQL) |
| Data Portal | http://localhost:5192 | Portal PostgreSQL, Elasticsearch, Keycloak PostgreSQL, login setup |
| TORCH | http://localhost:5193 | `torch-data` |
| TORCH result files | http://localhost:5194 | `torch-data` (read-only file service) |

`blaze` and `data-portal` select the same Blaze service and volume. Changing
between these Compose profiles preserves its resources. HAPI has a separate
database and port. All volume names are scoped to the Compose project. The
services bind published ports to loopback. The application uses HTTP to reach
services; service lifecycle is controlled through Compose.

## Stopping services

Generation produces datasets. Loading a dataset into a chosen FHIR server is a
separate, explicit operation in the [workbench](../README.md#load-datasets-into-a-fhir-server). Keep variants with overlapping resource IDs in
separate targets. Stopping services retains their volumes. To stop one server:

```sh
docker compose -f web/compose.yml stop blaze
docker compose -f web/compose.yml stop hapi hapi-db
```

Changing profile flags does not stop already running services. To stop the whole
project while retaining data:

```sh
docker compose -f web/compose.yml --profile data-portal --profile hapi down
```

Blaze enforces referential integrity by default. For datasets intentionally
containing missing reference targets, set `BLAZE_ENFORCE_REFERENTIAL_INTEGRITY=false`
in the Compose environment when starting/recreating `blaze`. This affects the
shared Blaze used by the portal too. Other FHIR validation rules still apply.

## Portal login and architecture

The local portal uses HTTP on loopback only; no certificate installation is required.
The first portal start automatically creates the local user `dataportaluser` with
password `password` and display name **Data Portal User**. No manual account registration is needed. The workbench shows
the login next to **Open Data Portal**.
Read the generated username and password with:

```sh
docker compose -f web/compose.yml run --rm --no-deps --entrypoint cat portal-init /setup/credentials.txt
```

The output is JSON: use `username` and `password` on the portal sign-in page.
The setup volume retains the login across restarts. A full data reset that removes
both the setup volume and the Keycloak database recreates this default login on the next portal start.
Keycloak imports the realm only
when initializing its database. Manage subsequent login changes in Keycloak.
The local login has the portal's user, power-user and administrator roles.

The Data Portal includes the upstream UI, backend, PostgreSQL, Elasticsearch,
ontology initializer, Keycloak and its PostgreSQL database, HTTP proxy, and
availability updater. It connects its direct CQL broker to Blaze. The Data Node
side provides Blaze and TORCH extraction plus explicit FHIR Data Evaluator runs.
The ontology initializer downloads ontology version `v5.0.0` on first startup;
backend readiness depends on successful indexing.

This is a local synthetic-data deployment. The portal authenticates through
Keycloak; local FHIR/TORCH diagnostic endpoints use loopback access. Direct
internal CQL queries use Blaze. FLARE is needed for the alternative FHIR Search
cohort path; this deployment uses the CQL path. Central FDPG/DSF registration and
external terminology infrastructure require their own deployment configuration.
HAPI is an independent FHIR target; portal queries use Blaze.

## Explicit evaluation

The FHIR Data Evaluator service has zero default replicas. Run it explicitly
against Blaze after loading data:

```sh
docker compose -f web/compose.yml run --rm fhir-data-evaluator
```

Its bundled Measure counts distinct Patients by gender using the ontology's
`patient-gender` stratifier and continuous-variable measure score. The updater
maps that score to the Patient gender catalogue entry and rounds down to its
availability buckets (0, 10, 100, 1,000, ...). For example, 12 Patients produce
catalogue availability 10; fewer than 10 produce 0. Reports are uploaded to Blaze
and persist in its data volume. This small Measure covers Patient gender;
additional catalogue criteria require corresponding measures. FDE measures data
availability; FHIR profile validation is a separate operation. The availability
updater reads FDE reports and updates catalogue availability in Elasticsearch.
Its explicit invocation is:

```sh
docker compose -f web/compose.yml run --rm availability-updater
```

## Upstream maintenance

Service definitions extend the selected files under `upstream/`, pinned to
[Data Portal commit ce654d5](https://github.com/medizininformatik-initiative/dataportal/tree/ce654d58f02be004625234504af0315c33d8b294).
`upstream/source.json` records source paths, checksums and small corrections
to the Keycloak database health-check variables and backend proxy prefix.
The local HTTP proxy omits host-wide HSTS to preserve other localhost HTTP services. Overrides in
`web/compose.yml` scope volumes, bind loopback ports, bound memory, configure
local authentication, and connect the portal and extraction services to the
shared Blaze. The local initializer constrains the upstream OAuth client to the
portal origin and creates the local account. HAPI uses the
[8.12.0-2 starter](https://github.com/hapifhir/hapi-fhir-jpaserver-starter/tree/image/v8.12.0-2)
with its documented PostgreSQL configuration.

The availability updater uses the official upstream image `0.4.2`. It loads
ontology documents recursively under `elastic/`, including `elastic/content/`,
fails clearly when no ontology nodes are found, and skips empty update files.
See the [upstream release](https://github.com/medizininformatik-initiative/dataportal-availibility-updater/releases/tag/v0.4.2).

Renovate updates local image overrides in `web/compose.yml`. The pinned files
under `upstream/` are excluded from automated dependency edits; update them
together with their source revision and checksums when refreshing the upstream snapshot.

## Verification

`python3 web/integration/test_compose_profiles.py` checks all eight Compose-profile
combinations, loopback bindings, volume isolation and pinned upstream files.
The [availability integration check](../integration/AVAILABILITY.md) verifies
real FDE reports, the complete ontology, byte-identical repeated updates and
the expected catalogue value read back from Elasticsearch in separate stages.
For a running local portal containing synthetic female Patients:

```sh
python3 web/integration/smoke_portal_query.py /absolute/credentials.json
python3 web/integration/smoke_torch_profile.py PATIENT_ID
```

The first probe authenticates with Keycloak, checks that anonymous queries are
rejected, and compares a real portal CQL query with an independent Blaze FHIR
search. The second explicitly extracts the selected IDs and checks downloaded
TORCH output. These probes create a portal query and extraction artifacts.

The first ontology import and database migration can take several minutes.
Inspect `docker compose -f web/compose.yml logs init-elasticsearch dataportal-backend`
and check `http://localhost:5192/backend/api/v6/actuator/health` before running the portal probe. Plan Docker memory for both the
selected services and existing workloads using the resource budgets above.
