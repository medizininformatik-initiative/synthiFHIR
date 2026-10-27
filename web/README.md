# Web workbench prototype

The interface defaults to German. The language selector switches between German
and English and remembers the selection in this browser. Status messages and
interface errors follow the selected language; converter logs and validation
reports retain their original text. FHIR output uses the German KDS independently
of the interface language. Shared interface texts live in
[`catalog/options/de.json`](catalog/options/de.json) and
[`catalog/options/en.json`](catalog/options/en.json).

The local workbench converts the bundled starter or INTERPOLAR demo workbook
with the existing converter defaults. It uses React, TypeScript, Vite, Tailwind
CSS and a shadcn/ui Button, with FastAPI and a separate Python worker.

The workbench has three tabs: **Generate & convert**, **Runs**, and
**Provide & use data**. Starting a run successfully opens **Runs** and selects
that run. Tab changes preserve the current settings and dataset selection.
A completed run offers **Load into FHIR servers**, which opens the upload tab
with its available datasets selected. That tab provides a shared startup command for the Data Portal package and
a primary link to the portal. An expandable TORCH section contains its separate
readiness status and extraction API guide.
The Data Portal supports feasibility queries and cohort selection; the TORCH
link opens its API health status, with extraction instructions linked separately.

## Configuration editor

The configuration editor provides a continuous resource page with section links
and separate tabs for additional identifiers, Data Absent Reason (DAR),
IDs/repetitions/time shifts, terminology, and checks/output. Controls use the agreed option contract and DAR
catalogue. Unavailable controls remain visible, explain their dependencies, and
retain selected values. Help icons support hover, keyboard focus and click, including the pattern syntax
and sample-preview details. Tab
navigation supports the arrow keys, Home and End.

The first visit starts with contract defaults. Changes are saved automatically
in this browser after a short typing pause and when leaving the page. The status
shows whether the draft is saved or saving failed. Incomplete entries are retained
for subsequent visits; validation determines whether the configuration can be exported.
**Export file** downloads its
versioned UTF-8 `.config` file with uppercase variable names and comments in the
selected interface language. Inactive selections are commented out and retained
when imported again. **Import file** accepts `.config` and JSON
configurations and validates them before replacing the current draft. See the
[configuration format](catalog/options/README.md#properties-export-and-import)
for values, comments, DAR fields and identifier rules. Imports and **Restore defaults**
are saved automatically. Invalid drafts cannot be exported. Imports reject unknown fields, incorrect types,
unsupported schema versions, duplicate assignments, JSON members or rule IDs, empty DAR
codes and invalid identifier patterns. DAR code fields offer standard suggestions and accept
free text, including intentionally invalid codes. Optional FHIR validation reports vocabulary violations.

DAR rules offer three choices: unchanged, add DAR only when missing, or always
replace. Missing-only rules retain existing values and existing DAR, including
values derived during conversion. Always-replace rules replace both with the
selected DAR. Common Encounter rules apply unless an active inpatient or outpatient
rule overrides them for that field; unchanged in a class-specific rule inherits
the common rule. Generation follows the selected configuration; optional FHIR
validation separately evaluates the resulting resources.

DAR replacements require a field-specific code. Resource selection controls their
availability; existing DAR selections remain stored when unavailable. Conditional
codes display the applicable narrative or procedure condition. Identifier rules
retain their UUID when edited or imported and provide deterministic sample
previews. The preview uses the configured counter start and repetition 0 with example resource and
patient IDs, and caps displayed counter padding at 256 characters. The identifier system shows an example placeholder while empty and unfocused.
Resource choices show their FHIR resource type. Identifier rules can target inpatient
facility, department or ward/service encounters separately, and distinguish laboratory
observations from vital signs. Selecting a broader group includes its subgroups; a rule
adds only one identifier per matching resource even when selections overlap.
Encounter identifiers are created through configured identifier rules. The converter
does not add an implicit hospital case-number identifier. Resource IDs and
references are independent of additional identifiers. The Resources navigation
shows Organization indented below Encounter; its status follows the station
service-provider setting, and its link opens that setting.

Counter start sets the first `{count}` value for each rule (default: 1), including
padded forms such as `{count:08}`. Each new run begins at this value. The `{` and `}`
buttons insert escaped literal braces (`{{` and `}}`) into the pattern.
Pattern tokens use compact buttons with explanations on hover or keyboard focus,
and can be clicked to insert at the
cursor, replace selected text, or append when no cursor position is available.
Actual resource counters and collision checks belong to converter execution.

**Start conversion** submits the current editor configuration
as versioned Properties. The API validates it with the Java converter before queuing the job.
Each job keeps its own input, configuration and converter fingerprint. Later
editor changes apply to subsequent jobs. Invalid drafts disable the start action
for KDS conversion;
server-side validation errors are displayed without creating a job.

## Saved configurations

Use **Save editor configuration**, the
first action above the saved configurations, to save an executable editor draft
under a name. Configurations persist as JSON documents
in the workbench volume and are available across browser sessions and container
restarts. Names are unique regardless of letter case.

Select an entry and choose **Load into editor** to edit or run it. Confirming the
load replaces the current draft. **Replace with editor draft** updates the selected
saved configuration after confirmation. **Rename** changes its name; **Duplicate**
creates an independent copy of its saved settings under a new name. **Delete**
removes the entry from the list while retaining the editor draft and existing runs.
Deleted documents are retained under `configurations/.trash/` in the volume.

Each update checks the saved revision. If another tab changed the same entry,
cancel the action and choose the current entry before retrying. The list refreshes
automatically every five seconds while the page is visible and when returning to
the page. This keeps changes from other tabs visible without replacing the editor
draft or an open confirmation.
Changes to saved configurations never modify the snapshots of existing runs.
Import and export continue to use the shared versioned configuration format.

## Uploaded inputs

Use **Upload input** beside the input selector to add an `.xlsx` workbook using
the supported template, a `.zip` containing CSV input groups, or Synthea R4 patient
bundles as `.json` or a ZIP of JSON files. CSV files must
be directly at the archive root; use the converter’s table names and group prefixes.
Each group needs its `Person.csv`. CSV column, row and patient-assignment checks
use the same parser as conversion. Uploads are limited to 64 MiB (256 MiB expanded). The
workbench stores the original bytes in its persistent volume and runs the Java
converter's structural checks before adding the file to the selection. The
inspection lists table names and row counts, excluding each header.
Clinical consistency checks run during conversion according to the selected
configuration.

Uploaded inputs use the current editor settings. Each run receives its own input
copy and SHA-256 checksum. Filenames are display labels, not filesystem paths.
ZIP archives retain their original bytes; each run extracts its own input directory.

Synthea inspection counts patients and resources, rejects duplicate patient IDs
and identifies bundles without patients. The import skips those bundles. Each
patient bundle must contain one Patient with an ID. Choose **CSV (ZIP)** for faster
conversion with lower memory use, especially for many cases. Choose **Excel** to
edit the generated workbooks and convert them again. Both paths use the same German
mappings, table validation and KDS converter. Excel creation uses LibreOffice.
Results include CSV tables as a ZIP archive or editable workbooks and the effective
configuration. Source comparisons and projection-loss reports are included when
**Detailed source diagnostics** is enabled. The selected table format is
saved with the run and restored when loading its settings.

CSV downloads are compressed; processing still needs space for unpacked tables,
original sources and FHIR output. The worker streams tables into a ZIP and verifies
it before removing the unpacked CSV working copies. Run cleanup retains that ZIP.
Plan disk capacity for the complete run, not only the download size. The current editor settings
control the KDS conversion. Each snapshot also fingerprints the import scripts,
template and mappings.

## Generate Synthea inputs

Choose **Generate Synthea data** in the input selector. Set the requested population,
age range, sex, patient and clinician seeds, age reference date, simulation end,
US state/city and exported history. Zero history years exports the full history.
The supplied example uses one patient, ages 30–80, Massachusetts, seeds `20260912`
and 12 September 2026 for both dates, matching the standalone workflow.

Enable **Use the current timestamp as seed** to fill the patient and clinician
seeds from the Unix timestamp in milliseconds when submitting the run. Manual seed
fields are disabled while this option is selected; switching it off restores their
values. An explicitly configured single-person seed also uses that timestamp when
requesting one patient. The resolved values are saved with the run. Loading a run
into the editor selects its saved manual seeds, allowing reproducible generation.

Synthetic contact enrichment has its own fixed seed, `20260912`. Patient and
source Encounter IDs determine the per-case random sequence for room/bed movements;
department routing also considers the clinical source data. Identical source data,
generator rules and mappings reproduce the same contacts. The movement report
records the seed and rule/mapping metadata. Converting saved workbooks again uses
the contacts already stored in those workbooks.

Enable **Only patients with an inpatient stay** to require one individual hospital
stay lasting at least the selected number of days (default: 2; one day is 24 hours).
The stay must overlap the exported history window. History 0 includes all history.
Durations are not added across stays; other encounters of a selected patient remain
available. The criterion combines with the other patient-selection settings.

The run shows matching patients, candidates checked, attempts for the current
patient and the configured limit. After 100 consecutive unsuccessful candidates,
a notice suggests cancelling and relaxing the criteria. **Maximum attempts** is a
limit per requested patient. Exhausting it stops the generation with a failed
status; it does not proceed to KDS conversion with a smaller cohort. Cancellation
also stops an ongoing candidate simulation through the existing process-group
shutdown. A limit bounds candidate count, not the duration of a single simulation.

The Compose build applies `scripts/synthea-workbench/generator.patch` to the pinned
Synthea source and includes `InpatientSelection.java`. The resulting JAR fingerprint
is saved in each generation snapshot. The build runs selection boundary checks
against this JAR before assembling the runtime images.

Advanced settings provide living/deceased selection, the bundled patient-selection
modules, an optional single-person seed, simulation timestep, attempt limit,
veteran population and disease/care modules. Module and location choices come
from the pinned generator JAR. With no module selection, all modules run; core
Synthea modules always run. Clinical import requires diagnoses, so narrowly
selected modules or history may generate patients that cannot be imported.

Each generator control has an info button explaining its effect on original Synthea
data and on the KDS projection. Disease/care modules determine which processes
are simulated, rather than guaranteeing a diagnosis in every patient. Additional
patient-selection rules retain matching simulated patients; their criteria may
not remain visible after history filtering or KDS projection. Selection rules
have descriptive names and show their actual criteria.

Choose **Synthea FHIR – original data** to generate FHIR R4 JSON without Excel
or KDS conversion. The dataset download retains Synthea patient identities,
US addresses, organizations and clinicians. KDS configuration controls are
hidden for this output. The run records its generator settings and supports
loading into the editor, cancellation, resource inspection and downloads. No KDS validation or
clinical projection is performed. The displayed patient count excludes auxiliary
organization and clinician bundles.

Choose **KDS FHIR – convert Synthea data** to use the clinical import pipeline.
The current editor settings apply to the conversion. Seeds, dates, settings,
generator revision and hashes are recorded.
Runs show requested, actually exported and successfully imported source-patient
counts separately. Extra deceased patients can increase the exported count.

Standard generation accepts 1–1,000 requested patients per run; bulk generation accepts up to 100,000 patients per overall job. Selection allows up to 10,000
attempts per patient slot. Generation defaults to ages 18–80, five years of history and disabled overflow.
It uses one Synthea thread, a 1.5 GiB generator heap, a 1 GiB converter heap
and a 6 GiB worker container; jobs run sequentially. Generated FHIR, CSV ZIP or editable
workbooks, original Synthea bundles, workflow reports and logs are included in
the download. See the [Synthea workflow](../docs/synthea-workflow.md) for output
layout and seed semantics.

Memory exhaustion appears explicitly in the run details and run list. The worker
checks Java/Python memory errors and operating-system OOM events, including when
Synthea exits with code zero. A killed process without OOM evidence is identified
separately. Partial output may be incomplete; diagnostics are included in the download.

## Start a run or load a previous run

Starting uses the selected input and the current editor settings. A run contains
one configuration, saved as an immutable snapshot. Retrying a submission after a
network interruption returns the same run instead of creating a duplicate.

**Load into editor** on a completed, failed, cancelled or interrupted run restores
its saved input, dataset name, generator settings and configuration for review.
Loading starts no job. Edit the settings as needed and use the normal start button.
A new run uses the installed converter and generator; existing results remain
attached to their original runs.

## Inspect datasets and reports

Select a run to see **Datasets and reports**. Each embedded configuration variant
has its own dataset archive; independent saved configurations remain separate
runs. The overview shows unique Patient and resource counts, counts by resource
type, repeated resource IDs and resources without IDs. Inspection streams one
representation per output folder in this order: JSON, NDJSON, gzip JSON, bzip2
JSON, ZIP JSON, XML. Additional formats do not increase the counts. Counts do
not establish FHIR conformance or equality of resources sharing an ID.

Expand the report section for import, validation and projection summaries, then
use each report link for the complete file. Summaries cover reports up to 2 MiB;
all reports remain accessible through the file list. Search the file list for
workbooks, sources, options, reports or logs. File and dataset downloads stream
from disk. The complete run ZIP remains available alongside the separate FHIR
dataset archives. Failed runs retain available artifacts for diagnosis.

The worker records a persistent dataset manifest and archive checksum. It indexes
finished runs when idle and publishes each manifest atomically. The inspection
JVM has a 512 MiB heap and a five-minute timeout; an inspection failure remains
visible without changing the converter outcome. Run search filters by source,
configuration name, run ID or status. **Load into editor** restores the original input
and settings for review; existing datasets remain available independently of a new run.

Before starting a run, optionally enter a **Dataset name**. The name appears before
the source and configuration in dataset lists and is searchable in the run history.
An empty field uses the automatic label. Loading a run into the editor restores its name.
The name is display metadata and does not change generated FHIR content or resource IDs.

## Load datasets into a FHIR server

In **Load datasets into FHIR servers**, select an available Blaze or HAPI target
and one or more datasets from successful runs. The displayed address identifies
the target. Start **Load datasets** to queue the action; generation jobs and
uploads share the worker and run one at a time.

The worker checks the entire selection before transferring any data. Different
contents under the same resource type and ID within that selection stop the
upload and identify the conflict. Select compatible datasets or load them in
separate actions. Each later action updates existing resources with the same
IDs; resources absent from the selection remain on the server.

Each action retains the selected dataset checksums, run snapshots, target,
preparation result and upload log. Status distinguishes waiting, checking,
uploading, success, conflicts, failure and interruption. **Cancel** stops further
work. An interrupted or failed transfer can leave already uploaded transactions
on the server; it is not rolled back. Select the datasets again to explicitly
start another action. Reloading the browser does not cancel an upload.

Dataset labels combine the chosen name (or workbook filename), source and saved
run facts, for example `Demo · Synthea (Pat 10 · Cases 42 · Hist 0 · Age 18–85)`.
Patient and case counts come from the saved workbook Person/Fall entries; contact
sublevels are excluded. Native Synthea output uses its inspected Patient/Encounter
counts. When actual and requested populations differ, both are shown. History and
age are the submitted Synthea settings; history is always numeric, including zero.
Unavailable counts are omitted. New upload records retain these labels when the
source run is deleted.

The upload history initially shows the five latest completed actions and all active
uploads. **Load older uploads** reveals more completed entries. Details and full
server diagnostics expand on demand; a short error remains visible. Delete an
individual completed history entry or clear all completed history to remove its
local logs and working files. Uploaded resources remain on the FHIR server.

The worker uses pinned `blazectl 1.5.1`, with two concurrent transactions for
Blaze and one for HAPI. It prepares PUT transactions preserving resource IDs and
FHIR resources, selects one representation per output directory in the same
order as dataset inspection, and supports JSON, NDJSON, compressed JSON and XML.
Original dataset files and archives remain unchanged. Missing resource IDs are
reported before upload. FHIR server rules, including Blaze referential integrity,
apply to each transaction; failures are retained in the log.

Start the desired services using the [main README](../README.md#start-with-docker).
The workbench checks FHIR R4 transaction support through the fixed Compose
endpoints; server lifecycle remains controlled by Docker Compose.

## Start and use

```sh
docker compose -f web/compose.yml up -d --build
```

Open <http://localhost:5184>, choose an input, adjust the editor settings and
select **Start conversion**. Saved configurations and configuration files load
into the editor for review before starting. Select a run to view its status and
recent logs. **Snapshot** downloads the submitted configuration and input and
converter fingerprints. **Download** provides a ZIP containing FHIR results,
converter reports, effective options and logs, including for failed validation
runs. Formats, patients per output file and FHIR validation follow the submitted
configuration. Defaults are JSON and NDJSON, one patient per JSON bundle and
FHIR validation disabled. See [converter results](../docs/converter-usage.md#output).

**Download configuration and Excel** provides a compact ZIP for conversion from
saved workbooks. It includes the submitted configuration, available effective
configuration files and environment reports, converter fingerprint, and file
checksums. Use the identified converter version and the saved output settings.
Externally referenced document files must be supplied separately at their expected
paths. Source comparison and upstream generation require the original sources.
For failed runs, the archive contains the workbooks produced before failure.

**Cancel** cancels queued work or terminates the running converter process group.
Reloading or closing the browser leaves work running. A worker restart marks
unfinished running jobs as `interrupted`; start a new conversion to retry.
Queued jobs remain queued and run when the worker is available.

The named `workbench` volume persists SQLite, immutable per-run
input/configuration copies, logs and results across container recreation.

```sh
docker compose -f web/compose.yml down
```

The workbench listens on the local loopback interface. It is intended for one
local user. The web service publishes the workbench port; API and worker communicate
through SQLite and persistent files. Neither service receives the Docker socket.
The workbench offers the two bundled inputs and uploaded workbooks, CSV archives and Synthea bundles with embedded,
editor settings.
Saved configurations and uploaded inputs are stored in the local workbench volume.
Optional FHIR servers and the local Data Portal are selected with Compose profiles; see
[local services](deployment/README.md).

## Verify

```sh
mvn test package
python3 -m pip install -r web/backend/requirements-test.txt
python3 -m unittest discover -s web/backend -p 'test_*.py'
npm ci --prefix web/frontend
npm run build --prefix web/frontend
python3 web/integration/smoke_workbench.py
```

The smoke test requires the running Compose project. It creates real conversions,
checks FHIR downloads and immutable snapshots, cancels queued and running jobs,
recreates this project's API to check proxy reconnection, and kills/restarts
this project's worker container to check recovery.

## Architecture and resource boundaries

The verified prototype uses the selected React/FastAPI stack, a persistent SQLite
queue and a separate worker with one conversion process tree per run. This is the architecture
for the next workbench increments. The shared converter integration comes next;
bounded parallel execution follows the shared-state and resource-budget audit.

1. FastAPI copies the selected input into a UUID job directory and records the
   submitted editor configuration. It validates the configuration with Java,
   writes the JSON snapshot, then inserts the SQLite job.
   The snapshot includes the converter JAR hash. A queued job fails explicitly
   if its converter image changes before execution.
2. SQLite WAL and short `BEGIN IMMEDIATE` claims allow independent jobs without
   double claiming. One supervisor currently owns recovery and executes jobs
   sequentially. An exclusive lock prevents competing recovery supervisors.
3. Each job launches its own process tree with its own working directory and output root.
   The worker includes LibreOffice for the existing Synthea import pipeline;
   the API uses a smaller Java/Python image. Both use the same converter build
   and import assets. Input inspection runs serially with a 256 MiB JVM or Python
   address-space budget and a 60-second timeout.
   The worker gets two CPUs and a 6 GiB memory ceiling. Synthea uses a 1536 MiB
   JVM heap; conversion uses a 1 GiB heap without validation and 3 GiB with
   validation to load the bundled profiles. The ceiling is not reserved memory. Budget Docker VM memory for all
   simultaneously running services, including the optional portal and FHIR servers.
   The API gets one CPU and 512 MiB. These are prototype resource bounds, not
   capacity measurements for large generation or validation workloads.
4. `EncounterConverter` contains mutable static contact pointers, collections of
   secondary contacts, derived ends and a Location map. `ConverterOptions` has
   per-instance counters and caches. Root log appenders also belong to the JVM.
   Keep independent jobs in separate JVMs. Within-run parallelism needs a separate
   audit before splitting patients or complete bundles.
5. `FHIRValidator` builds HAPI contexts, terminology support and caches. Parallel
   validation multiplies their memory cost. Synthea already has internal
   concurrency; future scheduling must budget that together with JVM heaps.
6. Bounded parallel execution can add slots behind the existing transactional
   claim interface while retaining a single recovery owner. Multiple independent
   supervisors need leases/heartbeats and per-owner recovery first. Deterministic
   IDs, seeds, cross-resource validation and variant isolation belong to #84.

Shared option execution uses the same Java pipeline for Excel, CSV and web.
Persistent job execution and saved configurations are tracked in #78; expanded sources in #79. Data Node
lifecycle/import controls and TORCH/FDE user workflows follow in #81–#82.

## Versioned Data Node probe

`web/integration/data-node.lock.json` records the upstream commit. Download its
archive, extract it outside this repository, then prepare a fresh test directory:

```sh
python3 web/integration/prepare_data_node.py /absolute/upstream /absolute/node-test
docker compose -f /absolute/node-test/compose.yml up -d
```

The preparation script requires Python 3, OpenSSL and JDK `keytool`. It copies the
upstream `data-node/` directory and extends its Compose services instead of
maintaining independent service definitions. It generates a seven-day localhost
certificate and PKCS12 trust store, a disposable `prototype` / `prototype` Basic
auth account, and a small patient-count Measure. Use only generated test data.

The isolated project is `synthifhir-data-node-74`. Ports 5185–5188 are bound to
loopback: Blaze, TORCH, TORCH's file server, and the TLS reverse proxy. Its named
volumes persist Blaze data and TORCH output. FDE uploads reports to Blaze;
`verify_fde.py` exports the latest reports into `node-test/fde-output`.
The TORCH test has a 2 GiB JVM heap and a 3 GiB container memory limit.
Absolute host paths identify bind mounts; these must be available to the Docker
host. Run the preparation and probe scripts on the host.

This data-plane probe uses direct internal FHIR access with OAuth issuer settings
empty. TORCH's external proxy route requires Basic auth over locally verified
TLS. Full upstream Keycloak discovery/token exchange, external terminology,
FLARE cohort selection and DSF are integration gaps. Direct local service ports
are for diagnostics; this topology is not a production authentication design.

Extract a successful workbench download and choose exactly one `fhir`
variant directory, then run:

```sh
python3 web/integration/probe_data_node.py /absolute/node-test /absolute/fhir
docker compose -f /absolute/node-test/compose.yml run --rm fhir-data-evaluator
python3 web/integration/verify_fde.py /absolute/node-test 3
```

The probe converts generated bundles into PUT transactions retaining resource
IDs and references, saves import responses, checks unauthenticated TORCH access,
submits the upstream example CRTDL for explicit imported patient IDs, and saves
its asynchronous response, downloads the final NDJSON files and verifies the
extracted patient IDs. Inspect `torch-response.json`, `torch-result.json`,
`torch-downloads/` and `fde-output/`. Use the actual imported patient count as
the final argument to `verify_fde.py`. It must match all Patients currently stored
in that test server (3 for a fresh server loaded with only the bundled demo).
FDE reports data availability; it does not replace FHIR profile validation.

```sh
docker compose -f /absolute/node-test/compose.yml down
```

Use a separate project/target per dataset when resource IDs overlap. Start and stop services with Docker Compose. The web application communicates
with configured services over HTTP and has no Docker socket access.

## Validation evidence and remaining integration work

Status on 2026-10-02:

| Check | Result |
| --- | --- |
| Maven tests | Passed: 113 tests, no failures or errors. |
| Python queue, API and worker tests | Passed: 8 tests. |
| Frontend TypeScript/production image build | Passed; npm audit reported zero known vulnerabilities. |
| Compose workbench lifecycle | Conversion, reconnect, immutable snapshot, logs and ZIP download passed. API recreation with automatic proxy reconnect, queued/running cancellation and SIGKILL recovery passed. |
| Pinned Data Node startup | Blaze, TORCH, file server and TLS proxy started successfully. |
| Demo data import | Three patient bundles imported with HTTP 200; Patient count is 3. |
| Starter data import | Ten patient bundles imported with HTTP 200; all references resolve and all 272 unique resources were read back. |
| TORCH extraction | HTTP 202 submission followed by HTTP 200 completion; downloaded NDJSON contains 3 Patients, 2 Conditions and 5 Provenance resources. |
| FDE | Standard report counted 3 patients; the separate obfuscated report counted 5. Both were uploaded and exported locally. |
| TLS / Basic auth | Trusted local certificate accepted; untrusted certificate rejected; unauthenticated extraction rejected with HTTP 401. |
| Restart and container recreation persistence | Patient count, FDE reports, TORCH job/files and Workbench job/ZIP retained after restart and forced container recreation. |

The starter workbook includes six DocumentReference examples associated with
both cases `1` and `2`. Their `Fall-Nr` cells contain the text list `1,2`.
Blaze referential integrity checking is enabled for both workbook imports.

Full Keycloak/OAuth discovery and token exchange, FLARE cohort selection,
external terminology services and DSF integration remain separate work. These
results establish the isolated prototype data plane and do not establish the
complete upstream authentication workflow.

The implementation follows the [shadcn/ui Button](https://ui.shadcn.com/docs/components/radix/button)
and [Tailwind Vite integration](https://tailwindcss.com/docs/installation/using-vite).
The Data Node services come from the
[pinned upstream commit](https://github.com/medizininformatik-initiative/dataportal/tree/ce654d58f02be004625234504af0315c33d8b294/data-node).
The small FDE Measure uses the structure of the
[v1.3.3 basic Measure example](https://github.com/medizininformatik-initiative/fhir-data-evaluator/blob/v1.3.3/docs/example-measures/example-measure-1.json).

## Module layout

- `frontend/`: browser interface.
- `backend/`: API, persistent storage and conversion worker.
- `catalog/dar/`: reviewed DAR field contract; `source/`, `generator/`, `generated/` and `tests/`.
- `integration/`: workbench and upstream Data Node probes.
- `compose.yml`: local workbench services.

See [DAR catalogue maintenance](catalog/dar/README.md) for generation and verification.

See [configuration implementation TODOs](TODO.md) for agreed behavior and remaining work.


## Run files and history

Run details list JSON, NDJSON, XML, Excel, logs and other files in separate groups.
The format selection controls converted FHIR results in `fhir/`. Source bundles,
configuration snapshots and reports under `details/` remain JSON even when only
NDJSON output is selected.
Validation and projection reports appear below the result files. “Download entire
run” saves the output, logs and `snapshot.json` together. “Load input and settings”
restores the run input, generator settings and FHIR configuration in the editor
for editing and starting another run.

Completed, failed, cancelled and interrupted runs can be deleted after confirmation.
This removes their local input copies, results and logs. Existing FHIR server data
and upload history are retained. Queued/running jobs and runs used by an active
upload cannot be deleted. Shared source files in the input library are retained.

Run duration starts when the worker claims a job, excluding queue time, and ends
after result preparation. It appears in the run list and details, the log, and
`timing.json` in the run ZIP. Historical runs without recorded timing show no
estimated duration; an interrupted run with no recorded end time has no final
duration.

Upload history shows the HTTP response counts and original server diagnostics
reported by blazectl. Accepted and rejected counts refer to bundles, not patients.
A partial upload can leave successfully accepted data on the server. The full
upload log remains available, including for uploads whose local run was deleted.

## Updating the local workbench

Build and update API and worker together whenever converter or import-pipeline
files change. Both services must contain identical converter and importer assets:

```sh
docker compose -f web/compose.yml build output-init api worker web
docker compose -f web/compose.yml up -d api worker web
```

Wait for active runs and uploads to finish before updating. Submitted runs retain
the converter and importer fingerprints; execution rejects mismatched versions.
The Synthea comparison uses the bundled DAR and option catalogues.

### Run cleanup

**Clean up / delete** opens a choice for the selected run. **Clean up** is available
for completed or failed runs after dataset inspection. It retains configuration,
Excel workbooks or the CSV table ZIP, generated FHIR files, version metadata and a checksum inventory.
Referenced files are retained conservatively, including dependencies found in
other saved workbooks and datasets. Shared input-library files are retained.

Cleanup removes source data, intermediate files, logs and redundant ZIP archives.
Source comparisons require the original source data and cannot be repeated after
those sources have been removed. To repeat an Excel conversion, download
**Configuration and Excel**, then load a workbook with the saved configuration.
Original workbook/CSV inputs and generation settings remain loadable in the editor;
removed Synthea import files do not. Native Synthea runs have no Excel workbooks.

Dataset downloads and uploads remain available. A requested dataset ZIP is rebuilt
from checksum-verified result files and cached until the next cleanup. Its archive
checksum is updated when the ZIP is rebuilt. The complete run
download contains the retained files and is created temporarily. Active uploads
prevent cleanup. **Delete completely** removes the entire local run; FHIR-server
contents and upload history remain available.

### Patient repetitions

**Additional repetitions** creates copies of the input patients: zero keeps one
pass, two produces three passes. The first pass uses prefix + original patient ID
+ suffix. Additional passes append `-rep-1`, `-rep-2`, etc. Clinical references
follow the corresponding copied patient. Collisions are checked before output.
Resource counter starts and per-repetition clinical time shifts remain independent.
Use different prefixes or suffixes to distinguish separate DIZ runs.

### Reuse saved Excel workbooks

On a completed run, **Reuse saved Excel** loads all its saved workbooks as one
input set and restores its converter configuration in the editor. Set the dataset
name and a DIZ-specific patient ID prefix or suffix, adjust the configuration, and
start conversion. Repeat with another configuration to produce another dataset
from exactly the same workbooks. The input selector retains the saved workbook
set for subsequent runs.

This executes Excel-to-FHIR conversion without running Synthea or the Synthea
importer again. The clinical content and mappings already written into Excel stay
fixed. Synthea-derived workbooks retain the Europe/Berlin conversion time zone.
Each run owns byte-identical copies of its inputs, a checksum manifest, and its
configuration. Removing the source run does not remove those copies. The action
also works after run cleanup; failed runs may supply only their available subset
of workbooks. Native Synthea output without Excel cannot use this action.

**Load input and settings** on a Synthea generation run restores its generator
settings and runs Synthea again when submitted. Use **Reuse saved Excel** to
convert its existing workbooks instead.

Select an uploaded input and choose **Remove input** to delete its saved copy from
the input library after confirmation. Existing runs keep their independent input
copies and results, and the original file on your computer is retained. Saved
Excel sets can also be removed and recreated from an existing run. Bundled inputs
remain available.

### Replay identity audit

Run the real worker audit in isolated temporary storage:

```sh
docker compose -f web/compose.yml exec -T worker python < web/integration/smoke_replay_ids.py
```

It imports two bundled Synthea examples once, reuses their Excel workbooks with
different ID prefixes/suffixes and repetitions, and checks references, JSON/NDJSON
agreement, counter starts, input independence and cleanup. It writes no FHIR
server data and removes its temporary jobs. Assertions include unqualified
`{count}` identifiers remaining unique across workbooks.

Additional identifier counters start once per rule and run at the configured
value. They continue across patients, repetitions and workbooks in sorted filename
order; independent runs start again at the configured value. Collision checks also
cover all workbooks in the run.
Patient ID prefixes/suffixes affect
patient-derived resource identities; identical medication definitions can retain
shared identities.

### Bulk cancellation and resume audit

Run the integration test in a separate worker container without mounting existing data:

```sh
docker run --rm -i --memory=4g --cpus=2 synthifhir-workbench-worker:latest python - < web/integration/smoke_bulk_resume.py
```

The test uses temporary queue and output directories and fixed Synthea fixtures with
the real mapper and Java converter. It cancels through the API handler after the
second subrun has produced unpublished FHIR, then resumes through the API handler.
It checks unique, contiguous patient and encounter identifiers across both subruns,
checkpoint counters, ZIP checksums, unchanged published output and temporary-file
cleanup. A synchronization gate makes the cancellation deterministic. The test
runs in the `workbench-inputs` CI job.

### Run outcomes

Generation succeeds when conversion completes and produces its output files.
Source comparison differences and optional FHIR validation findings do not change
that outcome or suppress generated test data. Process failures and incomplete
imports still fail generation; the CLI returns a nonzero exit code for these
failures. Inspect validation reports when using the CLI for conformance checks.

Run details show generation and FHIR validation separately. Synthea conversions
also show import notes, source comparisons with differences, and comparisons that
could not be assessed. Notes covered by disabled resources or explicit DAR
replacement are accounted for separately. Import notes can indicate mapping work,
but do not automatically mean a missing mapping. A failing source comparison
reports its first difference for each source dataset and variant, not an exhaustive
count of differing fields. These summaries remain available after run cleanup.
Historical reports without independent comparison results show that assessment as
unavailable; their recorded execution status is retained.

### Source diagnostics

Synthea KDS runs generate tables and FHIR with source diagnostics disabled by default. Enable **Detailed source diagnostics** to compare the result with the Synthea source and retain detailed transfer reports. This increases runtime, memory and disk usage. The selection is saved with the run and restored when loading it into the editor. Input checks, conversion failures and missing-output checks always run. FHIR validation remains a separate configuration option.

### Bulk NDJSON generation

Run `bash web/prepare-bulk.sh` from the project directory before submitting a bulk
run. It stops the currently running optional Compose services (DataPortal and its
databases/authentication, TORCH, Blaze/HAPI and auxiliary services), keeping the
web UI, API and worker available. It records the stopped services locally. After
completion, `bash web/restore-services.sh` restarts exactly those services. Both
commands require queued/running generations and FHIR transfers to finish first.

Bulk admission requires at least 3 GiB of available Docker VM RAM. The interface
updates availability every five seconds; the API checks submission and the worker
checks again immediately before execution, including its container memory headroom.
Unavailable memory measurements block admission. If memory is still insufficient
after preparation, assign more RAM to Docker. Keep optional services stopped during
the run. The check reserves no memory and cannot prevent other applications from
consuming RAM later. Standard generation uses its existing admission behavior.

Choose **Bulk data generation** in the Synthea settings to generate up to 100,000
source patients. Set **Patients per subrun** to a multiple of 100 between 100 and
10,000. A final subrun may contain fewer patients. One start executes all subruns
sequentially within one overall job; its details list each subrun and seed range.
The worker processes at most 100 patients per internal chunk.
For KDS output, additional identifier counters continue across every patient, chunk
and subrun of the overall job. Completed chunks store the counter positions for
resumption; new series start at the configured values.
Identifier collisions across patients and chunks stop publication of the affected
chunk. A disk-backed index checks identifier ownership, including case-scoped
sharing, and is rebuilt from checksummed registration journals on resume.
Overall progress weights generation, KDS mapping and conversion (or native JSON
compression), and NDJSON writing equally per patient. A second
bar shows the current stage. The patient counter counts completed chunks; the
remaining-time estimate uses measured processing times rather than stage weights.
Choose **KDS FHIR** to apply the clinical configuration and German mappings, or
**Synthea FHIR** to retain the original US FHIR R4 resources. Both produce one
NDJSON file per subrun. **Compress with gzip** is enabled by default and writes
`.ndjson.gz` using gzip level 6. Disable it for a plain `.ndjson` file ready for
blazectl. The bulk output format is fixed; standard-run format and validation
controls are replaced by the bulk output settings. Each line contains a complete
FHIR transaction Bundle. Synthea organization and clinician bundles are retained;
collection bundles receive PUT requests using their existing resource IDs.
Resource values and decimal precision are preserved. Source diagnostics and
FHIR validation are disabled in bulk mode. Bulk runs do not create dataset previews, CSV downloads,
whole-run ZIPs or detailed reports. Standard runs retain their existing behavior.

Directory names use a Europe/Berlin timestamp, a sanitized dataset name and the unique run ID. Dataset names use portable ASCII letters, digits, hyphens and underscores and retain at most the final 24 characters. The timestamp uses `YYYYMMDD-HHMMSS`. Standard runs use the full run UUID without hyphens. Bulk runs use `<timestamp>_<dataset-name>_<patients>Pat_<series-id>`, where the series ID is the first eight characters of the initial run UUID. Continuations append `(2)`, `(3)`, and so on; these positions remain stable when earlier runs are deleted. The patient count describes the current run. Patient IDs remain inside the transaction Bundles.

Workbench runs publish reusable datasets in their named directory under
`outputGlobal/`. Standard runs export one ZIP
per dataset variant; bulk runs publish NDJSON files directly in the run directory, one file per subrun with the configured patient count (up to 10,000). The final subrun may contain fewer patients. Each NDJSON package has an English `-statistics.txt` report alongside it, listing resource occurrences, distinct resource IDs per resource type within the package, and encounters by class, parent/child relationship and contact level. Repeated IDs count once per type; resources without an ID are listed separately and contribute only to occurrence counts. Contained resources are excluded. Counts are collected while writing the package; reporting requires no additional pass over its contents. The interface shows a copyable result directory.
Inputs, logs, reports and intermediate files remain in the internal Workbench
volume. Standard-run downloads and reports remain available in the interface.
Deleting a run also removes its exported datasets and result directory. Cleaning up a run retains its exported datasets.

Compose prepares the output directory before starting the unprivileged services.
On Linux it grants the service user access using filesystem ACLs and retains the
checkout owner's access. Docker Desktop uses its shared-folder permissions.
No host UID configuration or manual permission changes are required on supported
writable local filesystems. Read-only shares cannot be used as output directories.

Completed 100-patient chunks are checkpointed and staged internally until the
subrun NDJSON file is closed and atomically published. An interrupted package
is rebuilt from completed chunks; generation does not restart those chunks.
Hidden `.manifest` sidecars retain checksums and generation checkpoints for resume.
The `-<count>Pat.ndjson.gz` suffix reports distinct Patient resources in the file.
Reference-only output retains clinical resources and reports `0Pat` when no Patient
resources are included. Files can be downloaded individually or selected for upload.

For blazectl, decompress the files while retaining the compressed copies:

```sh
gunzip -k *.ndjson.gz
```

Upload the resulting transaction-Bundle NDJSON files with `blazectl upload`.

Each chunk uses a distinct reproducible patient seed derived from the submitted
seed. A 10,000-patient subrun consumes 100 seeds; the next starts with the following
seed. The requested count refers to source patients; supporting bundles do not count
toward that total. For KDS output, clinical output selections, ID settings and a
common time shift apply. Additional patient copies and per-copy
time shifts are disabled. Synthea output uses the generator settings directly;
KDS configuration and synthetic consent apply only to KDS output. Overflow is disabled in bulk mode.
The processes finish between chunks, and successful chunks release their temporary
sources and tables. The final NDJSON files continues to consume disk space.

Only successfully completed chunks receive a `part-*` name. Hidden `.partial`
directories are incomplete and must not be imported or shared. A failed or cancelled
run retains completed chunks; failed working files remain with the run for diagnosis.
Deleting the run removes its job metadata and working files, but never deletes the
external result directory. Loading settings starts a new run, not a resume operation.

### Synthetic consent

**Add synthetic consent** is enabled by default in the Synthea KDS generation
settings and populates consent fields. Clear the checkbox to disable it. The
default KDS configuration generates ward organizations as separate resources and
references them from Encounter.serviceProvider. Patient identities are ranked by a versioned hash of the patient
seed and identity. The allocation is 50% full consent, 15% patient data only,
15% patient and insurance data, 10% full refusal, and 10% without documented consent.
Bulk generation assigns consent to complete 100-patient source blocks, including
patients retained internally for continuation. Standard runs use largest-remainder
rounding for their requested population. Matching seeds and source blocks reproduce
the assignment independently of how a bulk series is split into jobs.

The consent date is the first inpatient encounter within the configured history,
falling back to the first other encounter in that period. The history ends on the
simulation end date; zero history years includes the entire lifetime. Dates are
restricted to the patient's lifetime. Without a matching encounter, the history
start (or birth, if later) is used. With unlimited history, the fallback is the
simulation end date or death date, whichever is earlier. Consent is omitted when
no plausible date is available, including death before the history window.
Supplied consent fields remain unchanged. The five groups are set explicitly to
yes or no; patients without documented consent have no consent date.
The generation setting is saved with the run. Consent provenance is included only
when detailed source diagnostics are enabled. Bulk jobs do not create separate
consent reports.

To broaden medication output, select **Add medication statement** for medication
administrations. To emit ward organizations, select **Generate and reference
organization** under **Ward as service provider**. These are independent clinical
configuration settings.

### Bulk progress and resuming

The current status updates in place approximately every two seconds during generation
and conversion. It shows the phase, selected/generated patients or converted files,
and selection attempts where available. Console output records chunk and subrun
completion. No per-patient console messages are displayed.

After the first completed chunk, the interface estimates remaining generation time
and total compressed FHIR size from completed chunks. These estimates adapt as more
patients complete. They are approximate: patient histories vary, and packaging adds
some overhead. Generated FHIR bytes and the size stored in compressed packages are shown separately; these are not two retained copies.

A failed or cancelled overall job stops before subsequent subruns. **Resume overall
job** reuses its saved settings and seeds, skips completed chunks in published packages or loose chunk directories and
recreates only unfinished temporary work. Completed files and packages remain in
place. Resume requires the generator, converter and importer versions saved with
the job. Keep its local output files and job record until it finishes.

### Continuing a bulk series

Choose **Continue series** below the execution mode, or use the same action in a
completed run's details. Select a previous run; the form loads the last block in
its continuation chain. Set the additional patient count and start the next block.
All other generation and converter settings are inherited and read-only. Identifier
rules display their next counter starts.

Continuation requires a successfully completed block with a saved completion state
and, for partial source blocks, its checksummed source remainder. The generator produces complete 100-patient source blocks and retains unused patients
internally. Continuation consumes this remainder before generating another block.
Consent assignments are retained from the complete source block; identifier counters
advance only during conversion of requested patients. The population seed advances
once per consumed 100-patient source block; the clinician seed stays unchanged. Each identifier rule continues
from its actual allocated counter, including allocations for contacts that are not
emitted. The backend also carries the identifier collision index into the new job.
Generator, converter and importer versions must match. An unfinished successor must
be completed or resumed before adding another block; the same predecessor cannot
start two successors. Keep the latest block's job record for continuation.

The isolated integration test compares 300 patients in one job against 275 followed
by a 25-patient continuation without regeneration. It uses seeded source fixtures and the real mapper,
converter, consent enrichment, counter restoration and NDJSON publication, comparing
uncompressed FHIR bytes and identifier uniqueness:

```bash
docker run --rm -i --memory=4g --cpus=2 synthifhir-workbench-worker:latest python - < web/integration/smoke_bulk_continuation.py
```

### Uploading bulk packages

In **Provide & use data**, start Blaze or HAPI, select completed datasets and
submit an upload. Search by run name and use **Select all matches** to select all
packages of a run. The packages use the same dataset selection as standard results.

The uploader scans the selected resources for conflicting contents before writing
to the server. For bulk NDJSON files it reads one Bundle line at a time, decompressing gzip when enabled; it does not extract archives or create full upload
copies. A temporary on-disk resource hash index supports conflict detection.
Ordinary datasets in a mixed selection retain their existing format conversion.

Each patient is sent as a FHIR transaction with stable-ID PUT requests. Progress
shows checked bundles, confirmed transactions and completed packages. **Resume
upload** continues a stopped bulk upload to the same target after the last confirmed
patient. If a response was lost, that patient's PUT transaction may be repeated.
Previously uploaded resources must still exist on the target; after clearing a
server, start a new upload. Generation and upload are explicitly separate actions.
