# Converter option contract

`contract.json` defines the agreed controls, defaults, dependencies and processing
rules for the configuration editor and shared Java converter. The contract records the agreed behavior. Issues #76 and #77 deliver the
editor and converter integration.

The Java converter owns execution semantics. Backend and frontend adapters use
the contract to present settings and pass selected values; they do not implement
contact assignment, transformations or identifier generation independently.
Bindings identify existing Java properties and CLI inputs. A binding does not
claim that the current converter already supports the full target behavior.

## Interface languages

The interface defaults to German and offers English. `de.json` and `en.json`
contain the visible labels, choices, help, disabled-control reasons and interface
messages. The contract references stable text keys; option IDs and values remain
language independent. DAR field/code labels are resolved using the key patterns
in `dar`; its clinical catalogue remains the source for suggested codes and representation rules.

The language setting affects presentation only. FHIR output retains the German
KDS profiles, terminology bindings and German descriptions/display text. Original
converter logs and validation reports retain their source language. Configuration
schema annotations use English independently of the current interface language.

The localization test checks every required key, rejects missing/unused/duplicate
keys and verifies matching placeholders in both languages. Add a new text key to
both language files when adding a control or message.

## Configuration values

A configuration contains `schemaVersion`, a `values` object keyed by option ID,
a `dar` object keyed by DAR field ID and an `identifierRules` array. Missing
values use contract defaults. JSON Schema describes accepted shapes; the Java
adapter applies defaults and validates clinical dependencies. Unknown keys,
wrong types, unsupported versions and conflicting duplicate values are errors.
Saved configurations contain converter settings; FHIR profiles define resource
structure and constraints.

Controls remain visible. Disabled selections retain their stored values. The
effective configuration disables unavailable settings and explains why, without
substituting another resource or contact level. Choice dependencies apply to
individual choices as well as entire controls. A selected unavailable parent or
reference target becomes ineffective while its selection is retained.

```json
{
  "schemaVersion": 1,
  "values": {
    "resource.Patient.mode": "reference-only",
    "reference.Condition.encounter": "department",
    "checks.fhirValidation": true,
    "output.formats": ["JSON", "NDJSON"]
  },
  "dar": {},
  "identifierRules": []
}
```

Resource selection controls all output. IDs use the stable potential-resource
sequence, so deselecting a resource does not renumber the retained resources.
Reference-only modes can refer to existing external resources. Medication and
Location can combine an external reference with descriptive identifier/display
information in the same Reference. Descriptions use the existing resource
identifier, or its generated ID when no identifier exists, plus the available
product text or location name. Known references to deselected internal resources
are removed; absolute external references are retained. Clinical timestamps are shifted together in
whole days, including birth date.

## Properties export and import

The editor exports `converter-configuration.config` as UTF-8 Properties text.
Descriptions, choice explanations, defaults and inactivity reasons use the selected
interface language. Names and values are language independent. The contract's
`propertyName` on each option is its stable uppercase export name. Existing Java
property names are reused where value semantics match; level selectors and other
expanded controls have their own names. The version line is required:

```properties
CONFIGURATION_VERSION = 1

# Patient output and references
# generate-reference: generate the Patient and references to it.
# reference-only: retain references to an existing Patient.
# neither: omit the Patient and references to it.
PATIENT_MODE = generate-reference

MEDICATION_ADMINISTRATION_ENABLED = false

# Inactive because MedicationAdministration output is disabled.
# MEDICATION_ADMINISTRATION_TREATMENT = add-statement
```

The configuration is a separate text file supplied explicitly to the converter.
Comments are wrapped into separate lines. Strings use Java Properties escapes for
backslashes, line breaks, tabs and leading spaces; literal Unicode is retained.
Booleans use `true`/`false`, integers use decimal notation, enums use the contract's
technical values and sets use comma-separated values (an empty set has an empty
right-hand side). Strings are unquoted. All options and all DAR fields are exported,
including defaults. Missing options use contract defaults.

When a control or its selected choice is unavailable, its assignment is prefixed
with `# `. Explicit effective `false` and `none` values remain active assignments.
The web importer reads recognized uppercase assignments in comments as stored
selections and reevaluates dependencies. Ordinary prose comments are ignored.
Duplicate assignments, including active/commented duplicates, are rejected;
edit the existing assignment rather than adding a second one. Unknown assignment
names, malformed values and unsupported versions also fail import.

A plain Java Properties reader ignores commented assignments. The shared
configuration adapter must also restore recognized commented selections before
evaluating dependencies, just as the web importer does. Otherwise an unavailable
selected contact level could fall back to a different, available default level.
Missing assignments use defaults; stored unavailable selections become ineffective
without substituting another choice.

External configuration files require `CONFIGURATION_VERSION=1`. The shared Java
reader rejects missing or unsupported versions and validates supplied settings. It validates stored values and determines effective
selections from the same contract. Directly equivalent Java properties are mapped to existing converter options.
Resource selection, diagnosis/procedure references, medication transformations,
DAR, identifiers and encounter rules use the shared Java execution pipeline.
Versioned configuration controls output formats, patients per file and FHIR
validation for Excel, CSV and web runs; these values replace the corresponding
CLI defaults and flags. The API uses the same Java preflight as file-based
configuration input. The web start action submits the current editor settings.


`propertiesFormat.darProperties` maps each DAR field ID to its uppercase name.
Each value is `unchanged` or an allowed field-specific DAR code. Missing DAR values
mean `unchanged`; import represents unchanged fields by their absence in the draft.
DAR assignments for resources not selected for output are commented out. The
export includes the field descriptions, allowed codes and clinical conditions.

Identifier rules use consecutive blocks starting at `IDENTIFIER_RULE_1_` with
`ID`, `ENABLED`, `RESOURCES`, `SYSTEM` and `PATTERN` assignments. `ID` preserves the
rule UUID; numbering preserves rule order. All five fields are required for each
block. Resources are comma-separated FHIR resource types. Rules that are disabled
or have no output resource selected retain their settings in commented assignments;
an explicit `ENABLED = false` remains active. Identifier patterns use the syntax
below and Properties escaping for literal backslashes and line breaks.

Import validates the entire draft before replacing the editor contents. JSON is
also accepted for saved drafts; browser persistence uses the configuration object.

## Contacts and references

DocumentReference offers three encounter assignment strategies:

- **Use only supplied encounters:** retain input encounter references and leave
  missing references absent.
- **Derive missing assignments from timestamps** (default): retain supplied
  encounters and use timestamp matching only for missing assignments.
- **Always derive assignments from timestamps:** recompute references even when
  encounters are supplied. If no usable timestamp or matching encounter exists,
  omit the reference, including any supplied reference, and report the reason.

The first two strategies report temporal conflicts without replacing supplied
encounters. Input encounters are identified by the combination of patient ID and case
number. A combination absent from the input is reported as a nonexistent
encounter. In preserving strategies, an unresolved supplied encounter is not
treated as a missing selection and silently replaced by timestamp matching. Contact levels apply to automatic matching;
supplied encounters are not moved to a different level. Selecting no encounter
reference omits the field in every strategy.

Matching requires the same patient and selected contact level. Start and end
boundaries are inclusive; a missing internal end is open. Among ambulatory and
inpatient contacts, a matching inpatient contact takes priority, including at a
later timestamp candidate. Ambulatory contacts provide the fallback. Within each
class search, timestamp candidates retain their order; the latest matching start
wins, then original input row order. Other encounter classes retain their existing
matching behavior. Deselected contacts are excluded from automatic targets. Missing matches
omit the reference and appear in the report. Contact levels are not substituted.

Immunization uses `occurrenceDateTime`. DiagnosticReport tries
`effectiveDateTime`, then `issued`. CarePlan uses `period.start`. These follow
the same next-candidate and contact matching rules.

Hierarchy is derived from the known input contact structure. When Encounter is
enabled with no selected level, generate one general case Encounter without a
KDS contact-level claim. Operation, consultation and examination/treatment
contacts remain excluded as ward/service clinical reference targets.

The department target and facility diagnosis defaults are project conventions;
generic Encounter targets in profile snapshots do not enforce these levels.

Java output projection applies these contact selections to JSON and NDJSON.
`encounterReferenceIssues` in the import report identifies omitted automatic
references, unresolved supplied DocumentReference contacts, omitted contact
targets and temporal conflicts, including resource identity and source row.
DocumentReference matching uses explicitly entered `Ausgabezeitpunkt`; its
automatically generated run timestamp and filesystem timestamps are excluded.

### Encounter classes and output end rules

`ENCOUNTER_AMBULATORY_ENABLED` and `ENCOUNTER_INPATIENT_ENABLED` select AMB and IMP
output independently across facility, department and ward/service levels.
Internal contacts remain available for derivations and stable resource identities.

Each class has `END_POLICY` and `END_APPLICATION` settings, for example
`ENCOUNTER_INPATIENT_END_POLICY = start`. Policies are:

| Value | Output end |
| --- | --- |
| `preserve` | Keep the input or internally derived end. |
| `open` | Remove the end. |
| `quarter-end` | Last day of the start date's quarter. |
| `year-end` | Last day of the start date's year. |
| `start` | Copy the start, including its precision. |
| `start-plus-second` | Add one second to the start. |

`END_APPLICATION = always` applies the policy to every selected contact;
`missing-input-end` applies it when the original row had no end value, including
a DAR-only end. An internally derived end does not prevent that application.

Calendar rules preserve date-only precision. For timestamps they use 23:59:59,
with nines at the existing fractional precision, and preserve the explicit UTC
offset. An offset does not identify a regional daylight-saving timezone.
`start-plus-second` uses UTC midnight for a complete date without a time; existing
timestamps retain their offset and fractional precision. Calendar calculations
and second arithmetic require a complete start date; missing or partial values
produce a diagnostic naming the contact, class and level. `start` can copy a
partial date. The bundled KDS Encounter constraints impose no positive minimum
duration for `start`.

End policies operate on output copies after internal derivations and reference
matching. They can intentionally produce child periods outside parent periods.
DAR is applied next: an active class-specific rule overrides the common Encounter
rule; `unchanged` inherits it. End policies determine `finished` versus `in-progress`
before DAR. DAR preserves those statuses and mirrors the final period to locations.
The import report records end-policy applications in `contactEndDerivations`.
Clinical date/time values, including birth date, shift by base days plus the
zero-based repetition index times repetition days before matching and end rules.
A shared primitive shifts once; resource metadata timestamps stay unchanged.
Date-only precision, time of day, fractions and explicit offsets are preserved.
A nonzero shift requires complete dates and reports partial dates without inventing
a month or day. `timeShifts` in the import report records the offset per patient
and repetition. Calendar end rules use the shifted contact start.

Identifier rules accept `Encounter.ambulatory` and `Encounter.inpatient` selectors
alongside `Encounter`. A rule matching both the common type and its class generates
one identifier. Pattern and hash expansion use `Encounter` as the resource type;
counters are shared per rule and reserved before output selection.

## Diagnosis references

Conditions are standalone resources. Disabling Condition output also disables
Condition diagnosis references and parent diagnosis inheritance. Stored inactive
selections remain available and are commented in the Properties export.

`CONTACT_DIAGNOSES_ROLES` selects codes from
`http://terminology.hl7.org/CodeSystem/diagnosis-role`, recorded in
`Encounter.diagnosis.use.coding.code`: CC, CM, AD, DD, pre-op, post-op and billing.
The role belongs to the contact association. All roles are selected by default.
Selected contact levels determine the targets automatically. An input contact
already at the target level keeps its reference. Assignment upward follows the
original parent hierarchy. Assignment downward uses `Condition.recordedDate`
(documentation time) to select at most one descendant per selected level, within
the original contact branch and patient case. Matching uses inclusive boundaries,
the latest contact start and then the earliest input row. Missing documentation
times and unmatched targets are recorded in `diagnosisReferenceIssues` in the
import report, with Condition ID, source contact, target level and iteration.
There is no fallback to another contact branch or level.

Existing references take precedence; a Condition is not added twice to a target.
Procedure references in the diagnosis list are handled separately. Conditions
remain standalone resources and are never duplicated by contact assignment.

Procedure references use their own enablement and selected contact levels.
Downward assignment uses the performed start within the original case hierarchy.

## DAR and transformations

DAR field IDs, clinical code choices, representations and conditional constraints
come from the [DAR catalogue](../dar/README.md). The option contract references
that catalogue rather than copying its 73 field definitions. Every field defaults
to unchanged. An enabled override replaces existing values and table DAR values
after derivations, using the catalogue representation and clearing replaced
values. Fixed/pattern values, coding-system discriminators and profile metadata
are preserved. Condition and Observation constraints still apply; do not remove
other clinical facts to hide a validation failure.

Table code strings remain unrestricted by this target contract, including
unknown `!dar:<code>` tokens. Structural and type checks still apply. Optional
FHIR validation reports invalid vocabulary codes; a valid but clinically
unsuitable DAR code can pass that validator. The editor offers field-specific suggestions and accepts custom codes for both
missing-only and overwrite rules. In configuration properties, `unchanged` disables
a rule; `!dar:unchanged` sets the literal code `unchanged`. A code starting with
`!dar:` is escaped with one additional `!dar:` prefix by the exporter.

MedicationRequest replacement runs first. MedicationAdministration and
MedicationStatement each offer one action: retain, additionally create the other
type, or replace with the other type. The latter two require the target resource
to be enabled. These actions run once on a snapshot after request replacement;
resources created during this pass are not transformed again. Both directions
can be configured without recursive conversions. Derived IDs are deterministic
and distinct from the original resources and from the opposite transformation.
Source patient identity and input contact context remain available to assignment.
Replacement removes references to the replaced internal source.

Transformations retain medication, subject, contact, notes, reasons and security
labels where available. An administration's effective time and actual dosage can
be carried to a statement. A statement's effective time can be carried to an
administration, but its regimen does not become an individual administered dose.
Request dosage instructions can be retained as a statement regimen. A request's
status and authored time do not establish administration or intake status and
time; planned request dosage is not copied as an administered dose. Shared event
status codes are retained where their meanings agree. Other status values and
unmapped fields are omitted and listed in the report. Modifier extensions without
a semantic mapping prevent transformation.

`medicationTransformations` in the import report records source, target, action,
unmapped fields and missing target facts. Required facts can remain absent;
FHIR validation reports these rather than the converter inventing them. Active
DAR overrides are applied after derivations and contact assignment, including
to derived medication resources.

Additional identifiers append to existing identifiers. Each rule has a stable
identity and one counter per run across input files, eligible resources and repetitions, unaffected
by output selection. Counts are reserved for original resources followed by
potential request and event derivatives, including stored actions whose output
dependencies are inactive. Omitted resources can therefore leave gaps. Shared
resources use their first occurrence’s patient context. The same logical resource serialized in several formats is
counted once. Duplicate system/value pairs across distinct logical resources or
repetitions fail with the rule and conflicting resource identities. Deterministic
hash identifiers do not guarantee secure pseudonymization. Hashes use the first 32 lowercase hexadecimal characters of SHA-256 with
collision checking. Patterns combine literal text with `{count}`, `{count:08}`, `{patientId}`,
`{resourceId}`, `{resourceType}`, `{iteration}` and `{hash}`. The shared counter
starts at the configured value (default 1), continues across input files in sorted
filename order, and resets for each independent run; the repetition index starts at 0. Padding sets a minimum width, not
a maximum. The Java runtime rejects padding above 1,000,000 characters with
a rule-specific error. `{{` and `}}` insert literal braces. Unknown tokens, unmatched braces
and malformed padding are errors; patterns do not evaluate expressions.

### Case-based additional identifiers

**Add case-based identifier** creates a rule with the same system, type and
target-resource selectors as ordinary identifier rules, including Encounter
levels. Its scope is exported as `IDENTIFIER_RULE_N_SCOPE=case`; omitted scope
means the ordinary resource-based rule.

Patterns accept literal text, escaped braces, `{patientId}` and `{caseId}`.
The default `{patientId}-{caseId}` combines the effective patient ID (including
prefix, suffix and repetition) with the original facility case/admission number.
All selected resources assigned to that case receive the same value. This can
reproduce local conventions that depart from FHIR or profile semantics.

The internal Encounter hierarchy identifies the case even when output hierarchy
levels are reduced. Assigned encounter references take precedence over source
case references. Multiple source references to the same facility case agree;
references to different cases are ambiguous. Resources without a unique case
receive no additional identifier; `outputSelections` in the import report
records `omit-identifier` with reason `missing-or-ambiguous-case`.
This note also appears among report reasons in the workbench.

Sharing is permitted within a case. Equal system/value pairs for different cases
still fail collision checks. Using `{caseId}` alone therefore requires case
numbers unique within the identifier system. Ordinary identifier rules retain
their per-resource counters and collision checks.

## Checks

Generate the structural configuration schema and check definition consistency:

```sh
python3 web/catalog/options/generate_schema.py
python3 web/catalog/options/generate_schema.py --check
python3 -m unittest discover -s web/catalog/options/tests -v
```

Verify that every existing Java option is represented, directly or by an explicit
adapter mapping, against a built converter:

```sh
java --class-path target/synthifhir.jar web/catalog/options/tests/CheckJavaBindings.java
```

Keep implementation and acceptance in #76/#77 distinct from this definition.
Parallelism and environment selection are execution settings outside the clinical
option contract.

## Encounter class configuration

The editor provides shared contact settings plus ambulatory (`AMB`) and inpatient
(`IMP`) groups. Each group selects output and an end policy: preserve input and
derivation, leave open, quarter end, year end, start, or start plus one second.
Policies apply to all three contact levels, either always or only when the
original input end is missing. Internally derived ends do not change that test.
The editor saves and exports these settings for the shared Java converter.

DAR provides common contact rules and class-specific overrides. An unchanged
class-specific field inherits the common rule. Additional identifiers can select
all Encounter classes or either class separately; both use `Encounter` for the
resource-type pattern token and the shared resource counter.

The configuration contract specifies output-end changes after temporal assignment,
with status alignment before DAR and period mirroring afterwards. Matching uses the original/internal periods,
with inpatient candidates preferred and ambulatory candidates as fallback.
Calendar policies use the shifted start. Start plus one second uses midnight only
when the full start date has no time. See [output end rules](#encounter-classes-and-output-end-rules)
for precision, incomplete-date handling and bundled profile constraints.

## Terminology version output

The Synthea version setting controls mapped coding versions: `catalogue-year`
passes the selected catalogue year through, while `omit` and `dar` omit the
importer's version value. With `dar`, configure the required coding-version fields
in the DAR section; a field without a selected override stays without an imported
version. Explicit manually entered workbook versions retain their usual input
semantics; selected DAR field overrides are applied during Java output projection.

## Observation unit error scenario

For laboratory observations and vital signs, **Output code in unit instead of code** deliberately reproduces a missing machine-readable unit code.
Each category has an independent switch, disabled by default. For UCUM-coded
`valueQuantity` and `component.valueQuantity`, the converter replaces `unit` with
`code` and removes `code`. It retains the numeric value, comparator and `system`.
Reference ranges, other code systems and quantities without a code are preserved.
DAR overrides are applied afterwards. FHIR validation may report errors for the
resulting test data, depending on the applicable profiles.


## Additional identifier types

Each identifier rule can set `USE`, `TYPE_TEXT` and `TYPE_CODINGS` as well as its
system and value pattern. `TYPE_CODINGS` is a JSON array of objects with `system`,
`code` and optional `display`. Several codings belong to one identifier type.
For example:

```properties
IDENTIFIER_RULE_1_USE = secondary
IDENTIFIER_RULE_1_TYPE_CODINGS = [{"system":"http://terminology.hl7.org/CodeSystem/v2-0203","code":"VN"}]
```

The editor suggests the complete type/use binding sets from the bundled FHIR,
German base and KDS profiles. `identifier-bindings.json` is generated by
`python3 web/catalog/options/generate_identifier_bindings.py`. Type suggestions
are the same for every eligible resource; systems, codes and displays are editable.
Profile-specific type restrictions are left to optional validation.

## Conditional DAR

Each DAR replacement has an independent “Only when no value is present” checkbox.
Its Properties setting is the DAR property name followed by `_ONLY_WHEN_MISSING`.
The default is false: the selected DAR replaces existing values. When true, values
already present after conversion and derivation are retained. Missing scalar
values and missing leaves of existing repeated items can receive DAR; repeated
names, codings or components are not invented. An existing DAR extension alone
counts as a missing value. Class-specific Encounter rules override the common
rule, including its condition.

## Ward service providers

`ENCOUNTER_STATION_SERVICE_PROVIDER` represents the input ward as an Organization
referenced by the ward/service Encounter. Choose `generate-reference` for a
separate Organization, `reference-only` for its external reference, `contained`
for an Organization embedded in the Encounter, or `none`. The default is
`generate-reference`.
With shared identity, Organization IDs are stable across encounters for the same
facility, department and ward. `ENCOUNTER_STATION_LOCATION` independently controls the ward Location
with physical type `wa` and defaults to true. Room and bed locations and the
Location output controls remain applicable.


## Patient-independent resource identities

`ORGANIZATION_IDENTITY`, `LOCATION_IDENTITY` and `MEDICATION_IDENTITY` each accept
`shared` (default) or `separate`. The three settings are independent.

Shared Organizations and Locations use the configured `PID_PREFIX` and
`PID_SUFFIX` as separate facility-key components, together with department and
place hierarchy. Configurations with the same prefix/suffix pair represent the
same synthetic facility; use different pairs for different facilities. Patient
identifiers, repetition counters and execution timestamps do not enter shared
identities. Identical inputs therefore reuse IDs across patients, packages and
runs. A bed belongs to its complete ward/room path: beds named “Bed 1” in different
rooms remain distinct. The actual parent reference also participates in identity.
The generated location hierarchy uses `partOf`; the ward Organization is referenced
by the Encounter's `serviceProvider`.

Shared Medications reuse the product definition, including product name,
code/system, ATC code/version, form and ingredient code/system. A matching name
alone does not establish product identity.

With `separate`, Organizations and Locations are scoped to their ward/service
Encounter. Medications are scoped to each MedicationRequest,
MedicationAdministration or MedicationStatement, including events added by the
medication transformation settings. Supported referenced medication ingredients
are copied into the same event context. Clinical events, their dosage and timing
remain separate and retain their values.

Each patient bundle includes its generated support resources. The same shared
resource may occur in several bundles with an identical ID and a PUT request;
these are repeated deliveries of one logical resource. Every package can thus be
uploaded independently. Explicit reference-only resource settings continue to
require the referenced resources on the target server. Concurrent synthetic stays
may reference the same shared bed.
