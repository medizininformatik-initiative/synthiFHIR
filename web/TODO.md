# Configuration workbench TODOs

This checklist records the agreed behavior and remaining implementation work.
GUI code, catalogues, tests and documentation live under `web/`. Shared converter
behavior remains in the Java converter. Existing issue references describe the
planned work streams; this checklist does not change their status.

## Profile-neutral and international FHIR output

- [ ] Extend the generator to support profile-neutral FHIR R4 test data and
  international CSV/Excel case specifications. This is a planned product
  extension independent of the MIE paper; international implementation-guide
  conformance requires separate verification.
- [ ] Inventory German-specific output assumptions across converters and the
  Synthea importer, including automatic MII profile claims, fixed code systems,
  extensions, terminology mappings and encounter structures. Distinguish explicit
  input from generated additions and document existing workbench controls.
- [ ] Agree on coherent output behavior and workbench controls before
  implementation. Retain the German KDS workflow and support generic output
  without unintended German profile claims or coding assumptions; do not add
  individual switches before agreeing on the overall approach.
- [ ] Verify representative international cases through the shared CSV/Excel
  pipeline, including terminology preservation, simplified encounters and
  emitted profile metadata. Existing generic Encounter output alone does not
  establish profile-neutral behavior for other resource types.

## Next bulk generation steps

- [ ] Regenerate all 10,000 patients in one sequential bulk run after the
  enrichment and packaging checks. Preserve the existing 3,000-patient output.
- [x] Group the internal 100-patient chunks into one NDJSON Gzip output file per subrun
  and show package progress within the single run; remove verified loose duplicates.
- [x] Load bulk packages into Blaze/HAPI with bounded memory, progress and resume.
- [x] Disable additional patient copies and per-copy time shifts in bulk mode;
  retain patient ID settings and the common clinical time shift.
- [x] Explain internal seed increments next to the start seed and display the
  effective seed range before submission.
- [ ] Review the prepared cohort settings and runtime before starting generation.

- [x] Support up to 100,000 patients per overall job with configurable subrun size,
  sequential execution, visible subrun states and checkpoint-based resumption.
- [x] Show phase progress, generated size and adaptive time/size estimates in place.

- [x] Keep additional identifier counters unique across bulk chunks and subruns,
  including cancellation/resumption; verify published packages remain unchanged in CI.
- [x] Show actual patient counts in ZIP names, whole-percent progress, the current
  subrun and a compact copy action for the local output path.

## Optional synthetic enrichment of Synthea data

- [x] Add reproducible optional synthetic Consent enrichment with a
  50/15/15/10/10 distribution, saved seeds and provenance.
- [ ] Inventory other content supported by the converter but absent from the
  Synthea input, and offer an explicit choice for each supported enrichment.
  Distinguish generating additional source content from selecting which existing
  content to emit. Agree the available choices, defaults and generated content
  before implementation; preserve supplied source data.

## Interface follow-ups

- [ ] Add small flag icons alongside the German and English language entries,
  preserving the explicit language labels.
- [ ] Add a light/dark appearance switch and apply the selected theme consistently
  throughout the workbench.

## Follow-up work recorded on 2026-10-08

Open items are ordered by the agreed next steps. Verified implementation is
summarized below with code/test evidence; the comprehensive output audit remains
open even where individual behaviors have regression coverage.
Complete the remaining clinical configuration audit before further performance
optimization. Native CLI guidance in the workbench follows the optimization work.

Generation must follow input and configuration, including intentionally nonconformant
test data. Profile/terminology conformance belongs to optional validation, not silent
correction or additional generator restrictions.

- [ ] Audit configuration-to-FHIR behavior across all relevant combinations:
  resource selection and defaults; contact hierarchy, timestamp candidates,
  diagnosis references and DocumentReference matching; medication transformations;
  DAR precedence, complex values, coding systems and fixed/pattern constraints;
  patient/resource IDs, repetitions, time shifts, identifier stability/collisions;
  terminology years, arbitrary table versions and emitted profile claims; output
  formats, consistency checking and optional validation. Reuse existing focused
  tests and add end-to-end evidence for uncovered cases.
- [ ] During the clinical audit, before performance optimization, review the entire
  codebase for automatically generated defaults and assigned coding systems that
  might need to be configurable for intentionally nonconformant test data. Record
  each candidate with its input path, actual output and existing configuration
  coverage, then discuss candidates individually with the user before changing
  behavior or adding options. Verify whether a real restriction exists: established
  correct mappings do not automatically need an override. Preserve the freedom to
  generate intentionally invalid data without introducing conformance restrictions.
  First candidate for discussion: DocumentReference.date uses the current run time
  when the input timestamp is absent (`DocumentReferenceConverter`); an explicit
  input timestamp and the existing date DAR option already control this field.
  Assess the intended default individually before changing it or adding an option.
- [ ] Complete a determinism audit across the pipeline, derived resources, formats
  and versions; distinguish clinical content from run/archive metadata.
- [ ] Broader memory/runtime tests with portal, Blaze and TORCH,
  including 1000 patients, remain deferred.
- [ ] After the optimization work, show native command-line instructions in the
  workbench for reproducing the selected run outside Docker. Include the matching
  input files, configuration, converter version, generation settings/seeds where
  applicable, and required commands. Preserve the same effective settings and
  clinical output. Native execution uses host resources rather than Docker limits;
  document its runtime requirements. Optimize first; implement this UI guidance later.
- [ ] Agree on compatible `mii-data-quality` rules and scope before integration.
- [ ] Complete the separately scoped Keycloak/OAuth, FLARE, external terminology
  and DSF integration.

## Awaiting external response

- [x] Send the BfArM inquiry about use and redistribution of derived UNII-to-ASK
  mappings. The user confirmed sending it on 2026-10-08.
- [ ] Await the BfArM response and clarify usage conditions, then review candidate
  substance matches, salts, hydrates and mixtures; avoid a dependency on MMI Pharmindex.

## Verified implementation

- [x] Accept explicit absolute system URIs alongside suggested system names in
  clinical input fields, including primary/additional codings, coded answers,
  medication products and ingredients. Emit custom URIs and free codes unchanged;
  retain named-system mappings and explicit versions. Excel diagnosis preflight
  and medication checks accept the same URI form. Template, starter and demo
  dropdowns allow custom entries; generated workbook lists do likewise. Evidence:
  `ClinicalSelectionWorkflowTest`, `ConditionConverterTest`, `DiagnosisWorkbookTest`
  and `test_custom_system_selections.py`. This resolves the agreed custom-system
  input case; other automatically assigned systems remain individual review items.
- [x] Audit DiagnosticReport timestamp candidates through CSV output
  (`ContactWorkflowTest`): effective time precedes issued time within a contact
  class, later candidates can match, inpatient candidates precede ambulatory
  fallback, and deselected inpatient contacts allow eligible ambulatory matches.
  Two patients and two cases exercise identity separation, missing/unmatched
  timestamps and explicit reference omission. DAR and output end rules preserve
  matching based on original timestamps. All contact workflow cases compare
  JSON/NDJSON resource content, including identical shared Location definitions.
- [x] Audit composed DAR behavior through CSV/workbook conversion and JSON/NDJSON
  (`ClinicalSelectionWorkflowTest`, `MedicationWorkflowTest`, `SelectionDarWorkflowTest`):
  missing-only rules retain negative measurements, custom units, free coded values
  and existing custom reasons; empty measurements receive the chosen reason.
  Numeric and nonnumeric rules target their respective supplied value types.
  Overwriting medication periods/doses removes their value components while keeping
  other fields and resource IDs. Scoped inpatient rules take precedence over common
  rules, including opposite missing-only flags. Contact periods retain the documented
  mirroring to location entries and preserve pre-DAR statuses. These cases introduce
  no new conformance restrictions or settings; the separate codewide default-value
  review remains open for individual discussion.
- [x] Close populated-input selection gaps (`ClinicalSelectionWorkflowTest`):
  Immunization, DiagnosticReport, CarePlan, Consent and DocumentReference are
  present in the baseline and individually omitted without changing other
  resources. Disabling laboratory or vital-sign observations removes only the
  matching DiagnosticReport results. JSON and NDJSON agree in every case.
  Optional Consent columns may be absent or blank with identical output; only
  explicitly supplied consent groups produce provisions. This fixes conversion
  failures when a dated consent contains only some optional group columns.
- [x] Check descriptive Medication/Location references through workbook conversion
  and derived administrations/statements (`SelectionDarWorkflowTest`): all four
  literal-reference/description combinations preserve selected resource IDs and
  target identities. Document attachment DAR preserves supplied data in missing-only
  mode and removes payload/hash/size on overwrite (`ClinicalSelectionWorkflowTest`).
  Additional timestamp-candidate coverage is recorded above; the overall clinical
  audit and individual default-value review remain open.
- [x] Audit deliberately invalid codes with the real bundled profile validator
  (`integration/smoke_validation_policy.py`): 16 CSV/Excel cases exercise profile
  validation and input consistency on/off. Invalid Patient gender and family DAR
  codes remain unchanged in JSON/NDJSON; validation reports both errors while
  generation succeeds. Missing required headers and unknown patient references
  fail with both consistency settings. Independent workbench outcomes agree with
  process results and reports. The native audit passes; the first Docker validation
  attempt was OOM-killed alongside the running portal services, so combined-load
  memory validation remains part of the deferred runtime tests.
- [x] Audit versioned configuration precedence over conflicting CLI output and
  validation flags, including default and configured snapshots
  (`ExecutionSettingsTest`, `CsvCommandLineTest`). Stored settings reproduce the
  selected format and grouping. CSV and Excel entry points create the validator
  according to the effective configuration, retaining the requested report level
  when configuration enables validation and avoiding profile loading when it
  disables validation. Supplied configuration files require CONFIGURATION_VERSION=1;
  empty, unversioned and unsupported-version files fail before conversion. Omitted
  configuration uses defaults and CLI execution settings. Excel/CSV entry points,
  direct file loading and the Synthea bridge share this rule. Configuration snapshots
  preserve current selections and comments; no alternate snapshot format is needed.
- [x] Audit output formats and patient partitioning with repetitions and date
  shifts (`OutputFormatsWorkflowTest`). JSON, XML, NDJSON, GZIP, BZIP2 and ZIP
  retain identical resource fields, IDs and transaction requests for file sizes
  1, 2, 4 and 1000. Fresh conversion and compressed-only output also agree.
  Partial final groups are finalized, and filenames account for repetitions to
  keep distinct groups from overwriting each other. Tests cover three original
  patients both with and without repetitions, shared medication resources,
  derived statements, Unicode and fractional doses. Archive timestamps are not
  treated as clinical content. The broader determinism audit remains open.
- [x] Batch consecutive, equally wide workbook rows in UNO writes of at most 512
  rows. The same 10 saved inpatient sources take 40.4 seconds for Excel creation
  versus 68.4 seconds with individual row writes (41% less); the import including
  conversion/comparison takes 98.4 versus 126.8 seconds, excluding Synthea generation.
  All 380 workbook content/style parts match both the stored baseline and the fresh
  unoptimized run byte for byte; only save metadata differs. All 20 JSON/NDJSON
  files match semantically and all 10 source comparisons pass. These are single
  local measurements. `scripts/tests/test_workbook_uno.py` covers text, operation
  boundaries and formatting beyond 1030 rows through real LibreOffice.
- [x] Measure the 10-patient inpatient workflow (history 10 years, minimum stay
  2 days, ages 18–80, patient/clinician seeds 20260912, default conversion,
  JSON/NDJSON, profile validation disabled): 141.5 seconds total; Excel creation
  66.2 seconds, conversion 34.4, Synthea/setup 17.9, comparison 8.0, mapping 5.6,
  remaining work 9.4. All 10 patients meet the duration in source and output;
  all 10 source comparisons pass. Single local measurement, not a general SLA.
- [x] Repeat that cohort with identical generator settings: all 10 source JSON
  contents, prepared worksheet rows and movement reports match exactly. Changing
  the patient seed to 20260913 produces 10 different patient IDs. Seven movement
  tests also pass, including separate processes and Python hash seeds. This is
  focused seed evidence; the comprehensive determinism audit remains open.

- [x] Optional inpatient patient selection with a minimum individual stay duration
  in 24-hour days, constrained by the export history window. Existing selection
  criteria and per-patient attempt limits apply together. Live attempt/match counts,
  a notice after 100 unsuccessful candidates and explicit failure on exhaustion
  make unsuccessful searches visible. Tests cover time boundaries, validation,
  progress parsing and stopping before KDS conversion after failed generation.

- [x] Audit explicit terminology versions and profile claims through serialized
  CSV output. `TerminologyWorkflowTest` compares JSON/NDJSON for ICD-10-GM, OPS
  and ATC years, free version text, omission and version DAR, including secondary
  diagnosis coding. Date shifts and Synthea mapping settings preserve explicit
  table versions; derived MedicationStatements claim their target profile.
  These checks verify emitted metadata, not full profile conformance.
  `test_terminology_year.py` covers Synthea mapping-year/output-policy separation.
  No converter defect was found in these combinations.
- [x] Verify synthetic movement reproducibility across separate Python processes
  and hash seeds (`test_movements.py`). Movement generation uses the fixed seed
  `20260912` with patient/source Encounter IDs and versioned rules, rather than
  directly using the editor's Synthea seed. Changing the movement seed changes
  synthetic contacts reproducibly. Reusing saved workbooks retains their contacts.
- [x] Audit IDs, repetitions and time shifts together in serialized output.
  `RepetitionWorkflowTest` covers numeric/UUID patient IDs with prefixes/suffixes,
  two cases per patient and two additional repetitions. JSON/NDJSON agree on
  66 unique object identifiers, 12 shared case groups, derived medication IDs,
  patient/case-local references and base-plus-iteration shifts (including a
  negative shift). Repeating the conversion preserves resource content and IDs;
  deselecting diagnoses, department contacts and derived statements preserves
  the identifiers of retained resources. Shared medication resources retain
  identical content. No converter defect was found in these combinations.
- [x] Audit medication transformations through serialized CSV-to-FHIR output.
  `MedicationWorkflowTest` compares JSON and NDJSON for bidirectional additions,
  request replacement, the combined request/event passes and deselected types.
  It verifies retained source facts, stable derived IDs, actual-dose versus
  regimen handling, reported omissions/missing event facts, local references,
  shifted event/contact periods, Medication reference-only/omission modes and
  DAR on derived doses. No converter defect was found in these combinations.
- [x] Audit contact/diagnosis/document policy composition through serialized CSV
  output. `ContactWorkflowTest` covers two patients with two cases each, transfer
  boundaries, diagnosis propagation to department/ward contacts, disabled
  departments with both ward-parent choices, procedure diagnosis levels and
  deselection, and all three DocumentReference assignment strategies (explicit
  time conflicts, missing/unresolved case numbers and missing source timestamps).
  Diagnosis references now carry explicit resource IDs before output policies
  resolve them; this fixes lost Condition/Procedure references in Encounter
  diagnoses. Focused contact tests additionally cover overlapping contacts,
  tie-breaking, secondary contacts, class selection and missing timestamps.
- [x] Audit selection/reference modes together with DAR through serialized
  Excel-to-FHIR output. `SelectionDarWorkflowTest` verifies Patient/Medication/
  Location generation, reference-only and omission, stable retained IDs, removal
  of references to deselected clinical resources, no resurrection through DAR
  or medication transformations, missing-only versus overwrite, custom codes,
  scoped inpatient precedence after end derivation, and coding-system retention.
  Every conversion compares JSON with NDJSON. The starter workbook directly
  exercises Condition, Procedure, Observation and MedicationRequest; derived
  administrations/statements cover medication transformations. Additional
  resource types and descriptive reference combinations remain covered by
  `ResourceOutputPolicyTest`, with missing leaves, existing DAR and complex
  values covered by `DarOverridesTest` and `EncounterOutputPolicyTest`.
  No converter defect was found in these audited combinations.

- [x] Case-based additional identifiers have a separate editor action and help,
  the existing target-resource selectors, and patient/case pattern tokens.
  Sharing is allowed within one patient/case, including Encounter hierarchy
  levels; different cases retain collision checks. Missing or ambiguous cases
  produce an omission note. Evidence: `CaseIdentifiersTest`, configuration
  roundtrip tests and `integration/smoke_replay_ids.py`.

- [x] Audit saved-workbook replay with two Synthea fixture patients, DIZ prefix
  and suffix configurations, two additional repetitions, Procedure start 700 and
  patient-qualified identifier counters starting at 500. Each replay produces six
  patients and 159 unique resources; JSON/NDJSON agree, references resolve, and
  linked clinical resources retain the same patient/repetition. Three identical
  Medication definitions are shared between DIZ outputs. Input copies survive
  source-run/input-library deletion; cleanup preserves output and further replay.
  Evidence: `integration/smoke_replay_ids.py`, plus existing numeric/UUID and
  collision tests in `ImportReportTest` and `ConverterOptionsTest`.
- [x] Share additional-identifier counters and collision checks across all workbooks
  and CSV input groups in one run. Each rule starts once at its configured value;
  repetitions and subsequent inputs continue it. Independent runs reset it.
  Evidence: `ImportReportTest` and `integration/smoke_replay_ids.py`, including
  unqualified `{count}` identifiers 500 and 501 across two saved workbooks.

- [x] Keep successful generation independent of source comparison differences and
  optional FHIR validation findings. Show validation reports and Synthea transfer
  notes separately, with configuration-covered omissions outside the import-note
  count. Comparisons account for resource selection, DAR and time shifts; each
  failing comparison reports its first difference rather than a total field count.
  Technical failures and incomplete imports fail generation. Persist summaries
  through cleanup. Evidence: `WorkflowRunTest`, `test_case_workflow.py`,
  `test_transfer_findings.py`, `test_run_outcomes.py`.

- [x] Preserve free table codes and nonempty `!dar:<code>` values, including unknown
  codes. DAR editor fields accept free text and suggested standard codes in both
  missing-only and overwrite modes. Structural checks remain active; optional FHIR
  validation reports vocabulary violations. Evidence: `InputCodeTest`,
  `ClinicalImportConverterTest`, `DarOverridesTest`, `CsvCommandLineTest`,
  configuration import/export tests.

- [x] Reuse all saved Excel workbooks of a completed run as one immutable input
  set; edit configuration and DIZ-specific ID prefix/suffix before starting a
  separate conversion without Synthea. Copies, checksums and source-run provenance
  survive source deletion. Evidence: `run_replay.py`, `test_run_replay.py`.

- [x] Run cleanup in the deletion dialog retains configuration, Excel, FHIR
  results, referenced files and checksums; removes source/intermediate files,
  logs and redundant archives. Downloads and FHIR upload remain usable, with
  dataset ZIPs rebuilt from checksum-verified result files. Active uploads block
  cleanup. Original source comparison requires original sources; repeat the
  Excel conversion using the retained workbooks/configuration. Evidence:
  `run_cleanup.py`, `test_run_cleanup.py`.

The evidence below records implemented behavior, not a claim that every possible
configuration combination has been checked.

- [x] DAR catalogue, field-specific choices and API configuration validation:
  `catalog/dar/generated/catalog.json`, `ConfigurationEditor.tsx`,
  `ContractConfigurationTest.defaultsAreExecutableAndDarIsSupported` and the API
  configuration preflight. `DarOverridesTest` covers missing-only/overwrite modes,
  complex values, coding-system preservation and scoped Encounter rules.
- [x] Common Encounter DAR rules are named explicitly. Inpatient/outpatient controls
  label the unchanged selection as inheritance. The existing inheritance behavior
  is covered by `EncounterOutputPolicyTest.unchangedScopedDarInheritsAndOtherClassesKeepTheirEnd`.
- [x] Versioned configuration contract, editor tabs, resource controls, help,
  import/export, saved configurations and identifier pattern editor:
  `catalog/options/contract.json`, frontend configuration tests and
  `ContractConfigurationTest`. Resource selection is implemented and covered by
  `ResourceOutputPolicyTest`, `PatientOutputPolicyTest` and `ContactOutputPolicyTest`.
- [x] Contact hierarchy, clinical reference assignment and diagnosis propagation:
  `ContactIndexTest`, `ContactOutputPolicyTest`, `ClinicalEncounterAssignmentTest`
  and `DiagnosisOutputPolicyTest`. These cover inclusive boundaries, tie-breaking,
  unavailable levels, secondary-contact exclusions, timestamp candidates and
  DocumentReference strategies. Review remaining edge cases in the full audit.
- [x] Medication replacement/addition order, stable derived IDs and resource
  selection: `MedicationTransformationsTest`.
- [x] Patient ID controls, terminology settings and clinical time shifts exist:
  `contract.json`, `ConverterOptionsTest`, `ClinicalTimeShiftTest` and
  `utils/TerminologyVersionUtilTest`. Repetitions append deterministic `-rep-N` suffixes for numeric IDs and UUIDs.
- [x] Additional identifier allocation, formatting, hashing, existing identifiers
  and collision checks: `AdditionalIdentifiersTest`.
- [x] Output/validation settings, per-run snapshots, variant archives and dataset
  inspection: `ContractConfigurationTest`, `ConverterVariantsTest`,
  `DatasetInspectionTest` and backend dataset/worker tests. JSON/NDJSON defaults,
  one patient per output file and disabled FHIR validation are in the contract.
- [x] Compact upload history: five recent completed uploads plus every active
  upload, collapsed details, visible concise errors, older entries on demand,
  deletion of individual/all completed local records and logs. Active uploads and
  FHIR server resources are retained. Evidence: `test_fhir_uploads.py`.
- [x] Compact configuration/Excel download for completed runs with saved workbooks:
  original bytes, available effective options/environment metadata, converter
  fingerprint and per-file checksums. Temporary ZIP files are removed after
  download. External document files referenced by workbooks must be supplied
  separately; upstream generation/source comparison requires original sources.
  Failed runs may contain only some workbooks. Evidence: `test_run_inputs.py`.
- [x] UI introduction/layout review, CLI `-p` default of 1, Compose lifecycle and
  bundled Data Node/TORCH/FDE checks, and the six starter DocumentReference case
  lists were completed in the existing checklist. See `README.md`,
  `CsvCommandLineTest` and `ConverterVariantsTest` for current behavior.
