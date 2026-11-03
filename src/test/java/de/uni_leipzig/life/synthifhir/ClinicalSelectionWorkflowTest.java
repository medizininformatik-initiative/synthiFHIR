package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.nio.file.*;
import java.util.*;
import org.apache.commons.csv.*;
import org.hl7.fhir.r4.model.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;

/** Covers clinical types absent from the starter workbook through serialized output. */
public class ClinicalSelectionWorkflowTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private Path input;

    @Before public void setup() throws Exception {
        input = temp.newFolder().toPath();
        table(TableIdentifier.Person, Map.of("Patient-ID", "p1", "Vorname", "Test", "Nachname", "Person",
                "Geburtsdatum", "2000-01-01", "Geschlecht", "weiblich", "Datum Einwilligung", "2026-01-01", "PDAT Einwilligung", "ja"));
        table(TableIdentifier.Fall, Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Start", "2026-01-01", "Ende", "2026-01-10",
                "Einrichtungskontaktklasse", "stationaer"));
        for (TableIdentifier type : List.of(TableIdentifier.Impfung, TableIdentifier.Befundbericht)) {
            var row = new LinkedHashMap<>(Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Eintrag ID", "event",
                    "Code", "example", "Codesystem", "SNOMED CT (Version nicht angegeben)", "Zeitpunkt", "2026-01-05", "Beschreibung", "Original text"));
            if (type == TableIdentifier.Befundbericht) row.put("Ergebnisse", "lab;vital");
            table(type, row);
        }
        table(TableIdentifier.Behandlungsplan, Map.of("Patient-ID", "p1", "Fall-Nr", "1",
                "Kategoriecode", "example", "Kategoriecodesystem", "http://snomed.info/sct",
                "Beginn", "2026-01-05", "Beschreibung", "Original text"));
        table(TableIdentifier.Laborbefund, Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Untersuchung ID", "lab",
                "Kategorie", "laboratory", "LOINC", "718-7", "Messwert", "12.5", "Einheit", "g/dL", "Zeitstempel (Abnahme)", "2026-01-05"));
        table(TableIdentifier.Klinische_Dokumentation, Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Untersuchung ID", "vital",
                "Kategorie", "vital-signs", "Untersuchungscode", "8867-4", "Wert", "70", "Einheit", "/min", "Zeitstempel", "2026-01-05"));
        table(TableIdentifier.DocumentReference, Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Dokumenttext", "Original document",
                "Ausgabezeitpunkt", "2026-01-05T00:00:00Z"));
    }
    private void table(TableIdentifier type, Map<String, String> row) throws Exception {
        table(type, List.of(row));
    }
    private void table(TableIdentifier type, List<Map<String, String>> rows) throws Exception {
        Set<String> headers = new LinkedHashSet<>(type.getMandatoryColumnNames());
        rows.forEach(row -> headers.addAll(row.keySet()));
        try (var writer = Files.newBufferedWriter(input.resolve("case_" + type + ".csv"));
                var csv = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
            csv.printRecord(headers);
            for (var row : rows) csv.printRecord(headers.stream().map(h -> row.getOrDefault(h, "")).toList());
        }
    }
    private Map<String, Resource> run(String settings) throws Exception {
        Path output = temp.newFolder().toPath();
        var converter = new Csv2Fhir(input.toFile(), output.toFile(), "case_", null,
                ConverterOptions.fromText("CONFIGURATION_VERSION=1\nOUTPUT_PATIENTS_PER_FILE=1000\n" + settings));
        converter.convertFiles(1000, OutputFileType.JSON, OutputFileType.NDJSON);
        assertFalse(new com.google.gson.Gson().toJson(converter.getImportReport().issues), converter.getImportReport().hasErrors());
        Map<String, Resource> json = new LinkedHashMap<>(), ndjson = new LinkedHashMap<>();
        try (var files = Files.walk(output)) {
            for (Path file : files.filter(Files::isRegularFile).toList()) {
                if (file.getFileName().toString().equals("case.json")) {
                    Bundle bundle = (Bundle)OutputFileType.JSON.getParser().parseResource(Files.readString(file));
                    for (var entry : bundle.getEntry()) add(json, entry.getResource());
                } else if (file.toString().endsWith(".ndjson")) {
                    for (String line : Files.readAllLines(file)) add(ndjson, (Resource)OutputFileType.JSON.getParser().parseResource(line));
                }
            }
        }
        assertFalse(json.isEmpty()); assertEquals(json.keySet(), ndjson.keySet());
        for (String key : json.keySet()) assertTrue(key, json.get(key).equalsDeep(ndjson.get(key)));
        return json;
    }
    private void add(Map<String, Resource> resources, Resource resource) {
        if (resource instanceof Bundle) {
            for (var entry : ((Bundle)resource).getEntry()) add(resources, entry.getResource());
            return;
        }
        String key = resource.fhirType() + "/" + resource.getIdElement().getIdPart();
        assertNull("Duplicate " + key, resources.put(key, resource));
    }
    @Test public void missingOptionalConsentColumnsMatchBlankColumnsWithoutGrantingOtherGroups() throws Exception {
        var missing = run("");
        var patient = new LinkedHashMap<>(Map.of("Patient-ID", "p1", "Vorname", "Test", "Nachname", "Person",
                "Geburtsdatum", "2000-01-01", "Geschlecht", "weiblich", "Datum Einwilligung", "2026-01-01", "PDAT Einwilligung", "ja"));
        for (String column : List.of("KKDAT retro Einwilligung", "KKDAT Einwilligung", "BIOMAT Einwilligung", "BIOMAT Zusatz Einwilligung"))
            patient.put(column, "");
        table(TableIdentifier.Person, patient);
        var blank = run("");
        assertEquals(missing.keySet(), blank.keySet());
        for (String key : missing.keySet()) assertTrue(key, missing.get(key).equalsDeep(blank.get(key)));
        var consent = (Consent)missing.values().stream().filter(Consent.class::isInstance).findFirst().orElseThrow();
        assertEquals(Consent.ConsentProvisionType.DENY, consent.getProvision().getType());
        Set<String> codes = new HashSet<>();
        for (var provision : consent.getProvision().getProvision()) {
            assertEquals(Consent.ConsentProvisionType.PERMIT, provision.getType());
            for (var code : provision.getCode()) for (var coding : code.getCoding()) codes.add(coding.getCode());
        }
        assertEquals(Set.of("2", "3", "4", "5", "6", "7", "8", "9", "37").stream()
                .map(code -> "2.16.840.1.113883.3.1937.777.24.5.3." + code).collect(java.util.stream.Collectors.toSet()), codes);
    }
    @Test public void individualSelectionsRemovePopulatedTypesAndPreserveOtherResources() throws Exception {
        var baseline = run("");
        for (var selection : Map.of("Immunization", "IMMUNIZATION", "DiagnosticReport", "DIAGNOSTIC_REPORT",
                "CarePlan", "CARE_PLAN", "Consent", "CONSENT", "DocumentReference", "DOCUMENT_REFERENCE").entrySet()) {
            String type = selection.getKey();
            assertEquals(type, 1, baseline.values().stream().filter(r -> type.equals(r.fhirType())).count());
            var output = run(selection.getValue() + "_ENABLED=false\n");
            Set<String> expected = new HashSet<>(baseline.keySet());
            expected.removeIf(k -> k.startsWith(type + "/"));
            assertEquals(type, expected, output.keySet());
            for (String key : expected) assertTrue(key, baseline.get(key).equalsDeep(output.get(key)));
        }
    }
    @Test public void observationSelectionPrunesReportResultsByCategoryAndRetainsClinicalContent() throws Exception {
        var baseline = run("");
        var original = (DiagnosticReport)baseline.values().stream().filter(DiagnosticReport.class::isInstance).findFirst().orElseThrow();
        assertEquals(2, original.getResult().size());
        for (Reference ref : original.getResult()) assertTrue(ref.getReference(), baseline.containsKey(ref.getReference()));
        for (String category : List.of("laboratory", "vital-signs")) {
            String option = category.equals("laboratory") ? "LABORATORY" : "VITAL_SIGNS";
            var output = run("OBSERVATION_" + option + "_ENABLED=false\n");
            var observations = output.values().stream().filter(Observation.class::isInstance).map(Observation.class::cast).toList();
            assertEquals(1, observations.size());
            assertFalse(category.equals(observations.get(0).getCategoryFirstRep().getCodingFirstRep().getCode()));
            var expected = original.copy();
            expected.getResult().removeIf(ref -> !output.containsKey(ref.getReference()));
            assertEquals(1, expected.getResult().size());
            String reportKey = "DiagnosticReport/" + original.getIdElement().getIdPart();
            assertTrue(expected.equalsDeep(output.get(reportKey)));
            for (String key : output.keySet()) if (!key.equals(reportKey)) assertTrue(key, baseline.get(key).equalsDeep(output.get(key)));
        }
    }
    @Test public void patientOnlySelectionEmitsOnlyPatientsInBundleAndNdjson() throws Exception {
        table(TableIdentifier.Klinische_Dokumentation, List.of("vital-signs", "social-history", "survey", "").stream()
                .map(category -> Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Untersuchung ID", "obs-" + category,
                        "Kategorie", category, "Untersuchungscode", "8867-4", "Wert", "70")).toList());
        var baseline = run("");
        assertEquals(5, baseline.values().stream().filter(Observation.class::isInstance).count());
        String settings = "ENCOUNTER_ENABLED=false\nCONDITION_ENABLED=false\nPROCEDURE_ENABLED=false\n"
                + "OBSERVATION_LABORATORY_ENABLED=false\nOBSERVATION_VITAL_SIGNS_ENABLED=false\nOBSERVATION_OTHER_ENABLED=false\n"
                + "LOCATION_MODE=neither\nMEDICATION_MODE=neither\nMEDICATION_REQUEST_ENABLED=false\n"
                + "MEDICATION_ADMINISTRATION_ENABLED=false\nMEDICATION_STATEMENT_ENABLED=false\n"
                + "IMMUNIZATION_ENABLED=false\nDIAGNOSTIC_REPORT_ENABLED=false\nCARE_PLAN_ENABLED=false\n"
                + "DOCUMENT_REFERENCE_ENABLED=false\nCONSENT_ENABLED=false\n";
        assertEquals(baseline.keySet().stream().filter(key -> key.startsWith("Patient/")).toList(),
                new ArrayList<>(run(settings).keySet()));
    }

    @Test public void attachmentDarRetainsDataInMissingModeAndRemovesPayloadMetadataOnOverwrite() throws Exception {
        var baseline = run("");
        String key = baseline.keySet().stream().filter(k -> k.startsWith("DocumentReference/")).findFirst().orElseThrow();
        var original = (DocumentReference)baseline.get(key);
        assertTrue(original.getContentFirstRep().getAttachment().hasData());
        String rule = "DAR_DOCUMENT_REFERENCE_CONTENT_ATTACHMENT_DATA=custom-mask\n";
        assertTrue(original.equalsDeep(run(rule + "DAR_DOCUMENT_REFERENCE_CONTENT_ATTACHMENT_DATA_ONLY_WHEN_MISSING=true\n").get(key)));
        var output = run(rule);
        var attachment = ((DocumentReference)output.get(key)).getContentFirstRep().getAttachment();
        assertFalse(attachment.getDataElement().hasValue()); assertFalse(attachment.hasHash()); assertFalse(attachment.hasSize());
        assertEquals("custom-mask", attachment.getDataElement().getExtensionByUrl(DarOverrides.URL).getValue().primitiveValue());
        assertEquals(baseline.keySet(), output.keySet());
        for (String other : baseline.keySet()) if (!other.equals(key)) assertTrue(other, baseline.get(other).equalsDeep(output.get(other)));
    }
    @Test public void measurementDarPreservesSuppliedValuesAndCustomReasonsInMissingMode() throws Exception {
        List<Map<String, String>> rows = new ArrayList<>();
        for (String kind : List.of("Zahl", "Code", "Fehlend", "Komponenten")) {
            var row = new LinkedHashMap<>(Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Untersuchung ID", kind,
                    "Kategorie", "laboratory", "LOINC", "718-7", "Zeitstempel (Abnahme)", "2026-01-05", "Werttyp", kind));
            if (kind.equals("Zahl")) { row.put("Messwert", "-123.5"); row.put("Einheit", "custom-unit"); }
            if (kind.equals("Code")) { row.put("Wertcode", "free-code"); row.put("Wertcodesystem", "LOINC"); row.put("Messwert", "Free text"); }
            if (kind.equals("Fehlend")) row.put("Messwert", "!dar:existing-custom");
            rows.add(row);
        }
        table(TableIdentifier.Laborbefund, rows);
        var baseline = run("");
        for (String variant : List.of("NUMERIC_MEASUREMENT", "MEASUREMENT")) {
            String rule = "DAR_LABORATORY_VALUE_X_" + variant + "=replacement-custom\n";
            var missing = run(rule + "DAR_LABORATORY_VALUE_X_" + variant + "_ONLY_WHEN_MISSING=true\n");
            var overwrite = run(rule);
            int kept = 0, replaced = 0, filled = 0;
            for (String key : baseline.keySet()) {
                Resource original = baseline.get(key);
                if (!(original instanceof Observation) || !"laboratory".equals(((Observation)original).getCategoryFirstRep().getCodingFirstRep().getCode())) {
                    assertTrue(key, original.equalsDeep(missing.get(key)));
                    assertTrue(key, original.equalsDeep(overwrite.get(key)));
                    continue;
                }
                Observation observation = (Observation)original;
                if (observation.getValue() instanceof Quantity) {
                    assertEquals(0, new java.math.BigDecimal("-123.5").compareTo(observation.getValueQuantity().getValue()));
                    assertEquals("custom-unit", observation.getValueQuantity().getUnit());
                } else if (observation.getValue() instanceof CodeableConcept) {
                    assertEquals("free-code", observation.getValueCodeableConcept().getCodingFirstRep().getCode());
                    assertEquals("Free text", observation.getValueCodeableConcept().getText());
                }
                if (observation.hasDataAbsentReason()) assertEquals("existing-custom", observation.getDataAbsentReason().getCodingFirstRep().getCode());
                var actualMissing = (Observation)missing.get(key);
                if (observation.hasValue() || observation.hasDataAbsentReason()) {
                    assertTrue(key, observation.equalsDeep(actualMissing)); kept++;
                } else {
                    var expected = observation.copy();
                    expected.setDataAbsentReason(new CodeableConcept(new Coding("http://terminology.hl7.org/CodeSystem/data-absent-reason", "replacement-custom", null)));
                    assertTrue(key, expected.equalsDeep(actualMissing)); filled++;
                }
                var expected = observation.copy();
                if (observation.hasDataAbsentReason() || observation.hasValue()
                        && (observation.getValue() instanceof Quantity) == variant.equals("NUMERIC_MEASUREMENT")) {
                    expected.setValue(null).setDataAbsentReason(new CodeableConcept(new Coding("http://terminology.hl7.org/CodeSystem/data-absent-reason", "replacement-custom", null)));
                    replaced++;
                }
                assertTrue(key, expected.equalsDeep(overwrite.get(key)));
            }
            assertEquals(3, kept); assertEquals(1, filled); assertEquals(2, replaced);
        }
    }

    @Test public void customSystemsSurviveMainAdditionalAnswerAndMedicationCodings() throws Exception {
        String main = "https://example.invalid/custom#v1", extra = "urn:example:extra";
        table(TableIdentifier.Diagnose, Map.of("Patient-ID", "p1", "Code", "nonsense", "Codesystem", main,
                "Zusatzcode", "also-wrong", "Zusatzcodesystem", extra, "Version", "custom-version"));
        table(TableIdentifier.Prozedur, Map.of("Patient-ID", "p1", "Prozedurencode", "nonsense", "Codesystem", main,
                "Zusatzcode", "also-wrong", "Zusatzcodesystem", extra));
        table(TableIdentifier.Laborbefund, Map.of("Patient-ID", "p1", "LOINC", "nonsense", "Codesystem", main,
                "Zusatzcode", "also-wrong", "Zusatzcodesystem", extra, "Werttyp", "Code",
                "Wertcode", "answer", "Wertcodesystem", "urn:example:answers"));
        table(TableIdentifier.Klinische_Dokumentation, Map.of("Patient-ID", "p1", "Untersuchungscode", "nonsense", "Codesystem", main,
                "Zusatzcode", "also-wrong", "Zusatzcodesystem", extra, "Werttyp", "Code",
                "Wertcode", "answer", "Wertcodesystem", "urn:example:answers"));
        table(TableIdentifier.Medikation, Map.of("Patient-ID", "p1", "Medikationstyp", "Verordnung",
                "Präparatcode", "nonsense", "Präparatcodesystem", main, "Wirkstoffcode", "ingredient", "Wirkstoffcodesystem", extra));
        for (TableIdentifier type : List.of(TableIdentifier.Impfung, TableIdentifier.Befundbericht))
            table(type, Map.of("Patient-ID", "p1", "Eintrag ID", "event", "Code", "nonsense", "Codesystem", main));
        table(TableIdentifier.Behandlungsplan, Map.of("Patient-ID", "p1", "Kategoriecode", "nonsense", "Kategoriecodesystem", main));
        table(TableIdentifier.DocumentReference, Map.of("Patient-ID", "p1", "Dokumenttext", "text",
                "Dokumentcode", "nonsense", "Dokumentcodesystem", main));
        var resources = run("CHECK_INPUT_CONSISTENCY=true\n");
        Set<String> checked = new HashSet<>();
        for (Resource resource : resources.values()) {
            CodeableConcept code = null;
            if (resource instanceof Condition) code = ((Condition)resource).getCode();
            if (resource instanceof Procedure) code = ((Procedure)resource).getCode();
            if (resource instanceof Observation) {
                code = ((Observation)resource).getCode();
                assertEquals("urn:example:answers", ((Observation)resource).getValueCodeableConcept().getCodingFirstRep().getSystem());
            }
            if (resource instanceof Medication) {
                code = ((Medication)resource).getCode();
                assertEquals(extra, ((Medication)resource).getIngredientFirstRep().getItemCodeableConcept().getCodingFirstRep().getSystem());
            }
            if (resource instanceof Immunization) code = ((Immunization)resource).getVaccineCode();
            if (resource instanceof DiagnosticReport) code = ((DiagnosticReport)resource).getCode();
            if (resource instanceof CarePlan) code = ((CarePlan)resource).getCategoryFirstRep();
            if (resource instanceof DocumentReference) code = ((DocumentReference)resource).getType();
            if (code == null) continue;
            assertEquals(main, code.getCodingFirstRep().getSystem()); assertEquals("nonsense", code.getCodingFirstRep().getCode());
            if (resource instanceof Condition || resource instanceof Procedure || resource instanceof Observation) {
                assertEquals(2, code.getCoding().size()); assertEquals(extra, code.getCoding().get(1).getSystem());
                assertEquals("also-wrong", code.getCoding().get(1).getCode());
            }
            if (resource instanceof Condition) assertEquals("custom-version", code.getCodingFirstRep().getVersion());
            checked.add(resource.fhirType());
        }
        assertEquals(Set.of("Condition", "Procedure", "Observation", "Medication", "Immunization", "DiagnosticReport", "CarePlan", "DocumentReference"), checked);
    }

    @Test public void unclassifiedObservationsIgnoreBothGroupsOfOptions() throws Exception {
        for (String category : List.of("", "social-history", "custom-category")) {
            for (var table : List.of(TableIdentifier.Laborbefund, TableIdentifier.Klinische_Dokumentation)) {
                boolean lab = table == TableIdentifier.Laborbefund;
                var row = new LinkedHashMap<String, String>();
                row.put("Patient-ID", "p1"); row.put("Fall-Nr", "1");
                row.put("Untersuchung ID", lab ? "lab" : "vital"); row.put("Kategorie", category);
                row.put(lab ? "LOINC" : "Untersuchungscode", "1234-5");
                row.put(lab ? "Messwert" : "Wert", "7"); row.put("Einheit", "entered unit");
                row.put("Einheitencode", "mg"); row.put("Einheitencodesystem", "http://unitsofmeasure.org");
                row.put(lab ? "Zeitstempel (Abnahme)" : "Zeitstempel", "2026-01-05");
                table(table, row);
            }
            var baseline = run("");
            var excluded = run("OBSERVATION_OTHER_ENABLED=false\n");
            assertFalse(excluded.values().stream().anyMatch(Observation.class::isInstance));
            var report = (DiagnosticReport)excluded.values().stream().filter(DiagnosticReport.class::isInstance).findFirst().orElseThrow();
            assertFalse(report.hasResult());
            String rules = "REFERENCE_OBSERVATION_LABORATORY_ENCOUNTER=none\n"
                    + "REFERENCE_OBSERVATION_VITAL_SIGNS_ENCOUNTER=none\n"
                    + "DAR_LABORATORY_VALUE_X_NUMERIC_MEASUREMENT=masked\n"
                    + "DAR_VITAL_SIGNS_VALUE_X_NUMERIC_MEASUREMENT=masked\n"
                    + "OBSERVATION_LABORATORY_UCUM_CODE_IN_UNIT=true\n"
                    + "OBSERVATION_VITAL_SIGNS_UCUM_CODE_IN_UNIT=true\n";
            for (String settings : List.of(rules, rules + "OBSERVATION_LABORATORY_ENABLED=false\nOBSERVATION_VITAL_SIGNS_ENABLED=false\n")) {
                var output = run(settings);
                assertEquals(baseline.keySet(), output.keySet());
                for (String key : baseline.keySet()) assertTrue(category + ": " + key, baseline.get(key).equalsDeep(output.get(key)));
                var observations = output.values().stream().filter(Observation.class::isInstance).map(Observation.class::cast).toList();
                assertEquals(2, observations.size());
                for (Observation observation : observations) {
                    assertTrue(observation.hasEncounter());
                    assertEquals("mg", observation.getValueQuantity().getCode());
                    assertEquals("entered unit", observation.getValueQuantity().getUnit());
                    assertEquals(!category.isEmpty(), observation.hasCategory());
                }
            }
        }
    }
    @Test public void carePlanContinuationRowsPreserveIndependentActivitiesAndStartNewPlans() throws Exception {
        var first = new LinkedHashMap<String, String>();
        first.put("Aktivitätsort", "Freier Ort");
        first.put("Aktivitätsende", "2026-03-05T11:00:00+02:00");
        first.put("Aktivitätsbeginn", "2026-03-04T10:00:00+02:00");
        first.put("Aktivitätsstatus", "completed");
        first.put("Aktivitätsstatusgrund", "Freier Grund");
        first.put("Aktivitätsversion", "v-custom");
        first.put("Aktivitätscodesystem", "urn:custom:activities");
        first.put("Aktivitätscode", "x"); first.put("Aktivitätstext", "Erste Aktivität");
        first.put("Kategorieversion", "v-category"); first.put("Kategoriecodesystem", "urn:custom:category");
        first.put("Kategoriecode", "k"); first.put("Kategorietext", "Kategorie");
        first.put("Erstellungsdatum", "2026-03-01"); first.put("Beginn", "2026-03-02");
        first.put("Titel", "Erster Plan"); first.put("Planstatus", "active"); first.put("Absicht", "order");
        first.put("Patient-ID", "p1"); first.put("Fall-Nr", "1");
        table(TableIdentifier.Behandlungsplan, List.of(first, Map.of(),
                Map.of("Aktivitätstext", "Zweite Aktivität", "Aktivitätscode", "free"),
                Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Titel", "Zweiter Plan"),
                Map.of("Aktivitätsstatus", "scheduled"),
                Map.of("Patient-ID", "p1", "Titel", "Ohne Fall")));
        var output = run("");
        var plans = output.values().stream().filter(CarePlan.class::isInstance).map(CarePlan.class::cast).toList();
        assertEquals(3, plans.size());
        CarePlan plan = plans.stream().filter(p -> "Erster Plan".equals(p.getTitle())).findFirst().orElseThrow();
        assertEquals(2, plan.getActivity().size());
        assertEquals("2026-03-01", plan.getCreatedElement().getValueAsString());
        assertEquals("2026-03-02", plan.getPeriod().getStartElement().getValueAsString());
        assertEquals("urn:custom:category", plan.getCategoryFirstRep().getCodingFirstRep().getSystem());
        assertEquals("v-category", plan.getCategoryFirstRep().getCodingFirstRep().getVersion());
        var activity = plan.getActivityFirstRep().getDetail();
        assertEquals("v-custom", activity.getCode().getCodingFirstRep().getVersion());
        assertEquals("Freier Grund", activity.getStatusReason().getText());
        assertEquals("Freier Ort", activity.getLocation().getDisplay());
        assertEquals("2026-03-04T10:00:00+02:00", activity.getScheduledPeriod().getStartElement().getValueAsString());
        var second = plan.getActivity().get(1).getDetail();
        assertFalse(second.hasStatus()); assertFalse(second.hasScheduled()); assertFalse(second.hasLocation());
        assertFalse(second.getCode().getCodingFirstRep().hasSystem());
        CarePlan other = plans.stream().filter(p -> "Zweiter Plan".equals(p.getTitle())).findFirst().orElseThrow();
        assertFalse(other.hasStatus()); assertFalse(other.hasCategory());
        assertEquals("scheduled", other.getActivityFirstRep().getDetail().getStatusElement().getValueAsString());
        CarePlan noCase = plans.stream().filter(p -> "Ohne Fall".equals(p.getTitle())).findFirst().orElseThrow();
        assertFalse(noCase.hasActivity()); assertFalse(noCase.hasEncounter());
    }

    @Test public void patientIdentifiersComeOnlyFromExplicitRulesAndDoNotChangeReferences() throws Exception {
        var baseline = run("");
        String patientKey = baseline.keySet().stream().filter(key -> key.startsWith("Patient/")).findFirst().orElseThrow();
        Patient original = (Patient)baseline.get(patientKey);
        assertFalse(original.hasIdentifier());
        assertTrue(original.hasId());
        String rule = "IDENTIFIER_RULE_1_ID=a152e771-3d5a-4cb1-9866-35fa6d91fd83\n"
                + "IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=Patient\n"
                + "IDENTIFIER_RULE_1_SYSTEM=https://example.org/patient-numbers\n"
                + "IDENTIFIER_RULE_1_PATTERN=patient-{patientId}\n";
        for (String code : List.of("", "MR", "custom-type")) {
            String settings = rule;
            if (!code.isEmpty()) settings += "IDENTIFIER_RULE_1_USE=secondary\n"
                    + "IDENTIFIER_RULE_1_TYPE_CODINGS=[{\"system\":\"http://terminology.hl7.org/CodeSystem/v2-0203\",\"code\":\"" + code + "\"}]\n";
            var output = run(settings);
            assertEquals(baseline.keySet(), output.keySet());
            Patient patient = (Patient)output.get(patientKey);
            assertEquals(1, patient.getIdentifier().size());
            Identifier identifier = patient.getIdentifierFirstRep();
            assertEquals("https://example.org/patient-numbers", identifier.getSystem());
            assertEquals("patient-" + original.getIdElement().getIdPart(), identifier.getValue());
            assertEquals(!code.isEmpty(), identifier.hasUse());
            assertEquals(!code.isEmpty(), identifier.hasType());
            if (!code.isEmpty()) {
                assertEquals(Identifier.IdentifierUse.SECONDARY, identifier.getUse());
                assertEquals(code, identifier.getType().getCodingFirstRep().getCode());
            }
            Patient expected = original.copy(); expected.addIdentifier(identifier.copy());
            assertTrue(expected.equalsDeep(patient));
            for (String key : baseline.keySet()) if (!key.equals(patientKey))
                assertTrue("Changed resource or reference: " + key, baseline.get(key).equalsDeep(output.get(key)));
        }
        var disabled = run(rule.replace("ENABLED=true", "ENABLED=false"));
        for (String key : baseline.keySet()) assertTrue(baseline.get(key).equalsDeep(disabled.get(key)));
    }
    @Test public void diagnosisAndMedicationIdentifiersComeOnlyFromRulesAndKeepReferences() throws Exception {
        table(TableIdentifier.Diagnose, Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Code", "example", "Codesystem", "urn:test:codes"));
        table(TableIdentifier.Medikation, Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Medikationstyp", "Verordnung",
                "Präparatcode", "example", "Präparatcodesystem", "urn:test:codes", "Präparatbezeichnung", "Product"));
        var baseline = run("");
        assertEquals(1, baseline.values().stream().filter(Condition.class::isInstance).count());
        assertEquals(1, baseline.values().stream().filter(Medication.class::isInstance).count());
        String rule = "IDENTIFIER_RULE_1_ID=a152e771-3d5a-4cb1-9866-35fa6d91fd83\n"
                + "IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=Condition,Medication\n"
                + "IDENTIFIER_RULE_1_SYSTEM=urn:test:identifiers\nIDENTIFIER_RULE_1_PATTERN={resourceId}\n";
        var output = run(rule);
        var disabled = run(rule.replace("ENABLED=true", "ENABLED=false"));
        assertEquals(baseline.keySet(), output.keySet());
        assertEquals(baseline.keySet(), disabled.keySet());
        for (String key : baseline.keySet()) {
            Resource original = baseline.get(key), expected = original.copy();
            Identifier identifier = new Identifier().setSystem("urn:test:identifiers").setValue(original.getIdElement().getIdPart());
            if (original instanceof Condition) {
                assertFalse(((Condition)original).hasIdentifier());
                ((Condition)expected).addIdentifier(identifier);
            } else if (original instanceof Medication) {
                assertFalse(((Medication)original).hasIdentifier());
                ((Medication)expected).addIdentifier(identifier);
            }
            assertTrue("Changed resource or reference: " + key, expected.equalsDeep(output.get(key)));
            assertTrue(key, original.equalsDeep(disabled.get(key)));
        }
        Medication medication = (Medication)baseline.values().stream().filter(Medication.class::isInstance).findFirst().orElseThrow();
        MedicationRequest request = (MedicationRequest)baseline.values().stream().filter(MedicationRequest.class::isInstance).findFirst().orElseThrow();
        assertEquals("Medication/" + medication.getIdElement().getIdPart(), request.getMedicationReference().getReference());
        var descriptive = run("MEDICATION_MODE=reference-only\nMEDICATION_EXISTING_REFERENCE=false\nMEDICATION_DESCRIPTION=true\n");
        assertFalse(descriptive.values().stream().anyMatch(Medication.class::isInstance));
        var reference = ((MedicationRequest)descriptive.get("MedicationRequest/" + request.getIdElement().getIdPart())).getMedicationReference();
        assertFalse(reference.hasReference());
        assertEquals(medication.getIdElement().getIdPart(), reference.getIdentifier().getValue());
        assertEquals("Product", reference.getDisplay());
    }
    @Test public void observationIdentifiersComeOnlyFromRulesAndKeepReportReferences() throws Exception {
        var baseline = run("");
        var observations = baseline.values().stream().filter(Observation.class::isInstance).map(Observation.class::cast).toList();
        assertEquals(2, observations.size());
        for (Observation observation : observations) {
            assertFalse(observation.hasIdentifier());
            assertTrue(observation.hasId());
        }
        for (String scope : List.of("Observation", "Observation.laboratory", "Observation.vitalSigns")) {
            String rule = "IDENTIFIER_RULE_1_ID=a152e771-3d5a-4cb1-9866-35fa6d91fd83\n"
                    + "IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=" + scope + "\n"
                    + "IDENTIFIER_RULE_1_SYSTEM=urn:test:observations\nIDENTIFIER_RULE_1_PATTERN={resourceId}\n";
            for (String type : List.of("", "OBI", "custom-type")) {
                String settings = rule;
                if (!type.isEmpty()) settings += "IDENTIFIER_RULE_1_TYPE_CODINGS=[{\"system\":\"http://terminology.hl7.org/CodeSystem/v2-0203\",\"code\":\"" + type + "\"}]\n";
                var output = run(settings);
                assertEquals(baseline.keySet(), output.keySet());
                int identified = 0;
                for (String key : baseline.keySet()) {
                    Resource original = baseline.get(key);
                    boolean selected = original instanceof Observation && (scope.equals("Observation")
                            || scope.equals(ClinicalEncounterAssignment.optionResource(original)));
                    if (!selected) {
                        assertTrue("Changed resource or report reference: " + key, original.equalsDeep(output.get(key)));
                        continue;
                    }
                    Observation observation = (Observation)output.get(key);
                    assertEquals(1, observation.getIdentifier().size());
                    Identifier identifier = observation.getIdentifierFirstRep();
                    assertEquals("urn:test:observations", identifier.getSystem());
                    assertEquals(original.getIdElement().getIdPart(), identifier.getValue());
                    assertFalse(identifier.hasAssigner());
                    assertFalse(identifier.hasUse());
                    assertEquals(!type.isEmpty(), identifier.hasType());
                    if (!type.isEmpty()) assertEquals(type, identifier.getType().getCodingFirstRep().getCode());
                    Observation expected = ((Observation)original).copy(); expected.addIdentifier(identifier.copy());
                    assertTrue(expected.equalsDeep(observation));
                    identified++;
                }
                assertEquals(scope.equals("Observation") ? 2 : 1, identified);
            }
            var disabled = run(rule.replace("ENABLED=true", "ENABLED=false"));
            for (String key : baseline.keySet()) assertTrue(baseline.get(key).equalsDeep(disabled.get(key)));
        }
    }
}
