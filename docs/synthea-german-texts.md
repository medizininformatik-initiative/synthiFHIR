# German clinical text

The importer uses `scripts/mappings/synthea-german-texts.json` for clinical labels,
text values and Synthea document phrases. Generation performs local table lookups.
Each entry records `original`, `de`, `source` and `review`, keyed by the source
system and code where available. A registry hash ties the table to its source inventory.

These are project-specific display texts for synthetic data. Entries marked
`pending-medical-review` require clinical review, including translations of
specialized procedures and questionnaire wording.

## Provenance

| Source | Use |
| --- | --- |
| Editorial translations | Clinical wording, symptoms and document phrases. |
| Medication dictionary | German ingredient and dose-form terms, retaining numeric and product details. |
| Wikidata | Selected labels matched by exact SNOMED code through property P5806; structured data under CC0. |
| OPUS-MT English–German | Machine-drafted entries with review status recorded in the table. |

The OPUS-MT source is Helsinki-NLP's
[`opus-2020-02-26` model](https://object.pouta.csc.fi/OPUS-MT-models/en-de/opus-2020-02-26.zip),
licensed under CC BY 4.0. The license is preserved in
[third-party/opus-mt/LICENSE.txt](../third-party/opus-mt/LICENSE.txt).
The table identifies editorial modifications and machine drafts. Official
terminology labels and these project translations have distinct provenance.

## Text handling

The importer translates label, text-value and document-text fields. Document names,
ages and smoking-cessation ages remain parameters. Clinical codes, statuses,
identifiers, times and measurements follow their structured input paths.

US insurance names such as Medicare remain proper names, labelled as insurance
from the source. Education and social categories receive German wording while
retaining their source meaning. Address observations with LOINC `56799-0` use the
same synthetic address as the patient.

The [identity projection](synthea-german-demographics.md) runs before translation.
Its `targetTextSha256` describes that intermediate text. `germanTexts` records the
translation-table version, hash, translated occurrence counts and unknown fragments
under `missing`. Per-document final hashes and derived processing statuses are recorded
in `documentMappings`; see [document classification](synthea-clinical-import.md#document-classification-and-processing-status). Observation and CarePlan fields use known German translations.
If an unfamiliar label has a known code, its German catalogue label is used.
Untranslated free text remains in the report and source bundle; its workbook field
stays empty when no German replacement is available. CarePlan activity descriptions
use existing dictionary entries; otherwise the activity's German code label remains.
Facility proper names, technical enum values and codes retain their specified spelling. Other text paths,
including document phrases and medication dosage text, retain unknown source text
for review.

## Review and maintenance

Improve wording in the versioned table and update its provenance and review status.
Tests check inventory coverage, medication numbers and product names, negation,
laterality, document parameters and structured-field preservation. The roundtrip
check compares complete final document text with the FHIR output.

Review the `missing` list and pending entries when introducing a new generator
revision or clinical scenario. Check long labels and document text in the workbook
as part of reviewing the generated case.
