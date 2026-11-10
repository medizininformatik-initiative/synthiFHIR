package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.nio.file.*;
import java.util.*;
import org.apache.commons.csv.*;
import org.hl7.fhir.r4.model.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;

/** Exercises contact policies together through CSV input and serialized FHIR output. */
public class ContactWorkflowTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private Path input;
    private ImportReport report;

    @Before public void setup() throws Exception {
        input = temp.newFolder().toPath();
        table(TableIdentifier.Person, List.of(patient("p1"), patient("p2")));
        List<Map<String, String>> contacts = new ArrayList<>();
        for (String patient : List.of("p1", "p2")) for (String number : List.of("1", "2")) {
            String month = number.equals("1") ? "01" : "02";
            contacts.add(Map.of("Patient-ID", patient, "Fall-Nr", number, "Start", "2026-" + month + "-01",
                    "Ende", "2026-" + month + "-10", "Einrichtungskontaktklasse", "stationaer"));
            contacts.add(Map.of("Patient-ID", patient, "Fall-Nr", number, "Start", "2026-" + month + "-01",
                    "Ende", "2026-" + month + "-05", "Fachabteilung", "0100", "Station", "A"));
            contacts.add(Map.of("Patient-ID", patient, "Fall-Nr", number, "Start", "2026-" + month + "-05",
                    "Ende", "2026-" + month + "-10", "Fachabteilung", "0200", "Station", "B"));
        }
        table(TableIdentifier.Fall, contacts);
    }
    private Map<String, String> patient(String id) {
        return Map.of("Patient-ID", id, "Vorname", "Test", "Nachname", "Person", "Geburtsdatum", "2000-01-01", "Geschlecht", "weiblich");
    }
    private void table(TableIdentifier type, List<Map<String, String>> rows) throws Exception {
        if (type == TableIdentifier.DocumentReference) {
            var documents = new ArrayList<Map<String, String>>();
            for (var source : rows) {
                var document = new HashMap<>(source);
                document.put("Dokument-ID", "document-" + documents.size());
                document.put("Embed", "Ja");
                documents.add(document);
            }
            rows = documents;
        }
        Set<String> headers = new LinkedHashSet<>(type.getMandatoryColumnNames());
        rows.forEach(row -> headers.addAll(row.keySet()));
        try (var writer = Files.newBufferedWriter(input.resolve("case_" + type + ".csv"));
                var csv = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
            csv.printRecord(headers);
            for (var row : rows) csv.printRecord(headers.stream().map(h -> row.getOrDefault(h, "")).toList());
        }
    }
    private List<Resource> run(String settings) throws Exception {
        Path output = temp.newFolder().toPath();
        var converter = new Csv2Fhir(input.toFile(), output.toFile(), "case_", null,
                ConverterOptions.fromText("CONFIGURATION_VERSION=1\nOUTPUT_PATIENTS_PER_FILE=1000\n" + settings));
        converter.convertFiles(Integer.MAX_VALUE, OutputFileType.JSON, OutputFileType.NDJSON);
        report = converter.getImportReport();
        assertFalse(report.issues.toString(), report.hasErrors());
        Bundle bundle = (Bundle)OutputFileType.JSON.getParser().parseResource(Files.readString(output.resolve("case.json")));
        var resources = bundle.getEntry().stream().map(Bundle.BundleEntryComponent::getResource).toList();
        List<Resource> ndjson = new ArrayList<>();
        try (var files = Files.walk(output)) {
            for (Path file : files.filter(path -> path.toString().endsWith(".ndjson")).toList())
                for (String line : Files.readAllLines(file)) {
                    Resource parsed = (Resource)OutputFileType.JSON.getParser().parseResource(line);
                    if (parsed instanceof Bundle) for (var entry : ((Bundle)parsed).getEntry()) ndjson.add(entry.getResource());
                    else ndjson.add(parsed);
                }
        }
        Map<String, Resource> byId = new HashMap<>();
        for (Resource resource : ndjson) {
            Resource previous = byId.put(resource.fhirType() + "/" + resource.getIdElement().getIdPart(), resource);
            if (previous != null) assertTrue("Conflicting shared resource", previous.equalsDeep(resource));
        }
        assertEquals(resources.size(), byId.size());
        for (Resource resource : resources) assertTrue("Format mismatch: " + resource.getId(),
                resource.equalsDeep(byId.get(resource.fhirType() + "/" + resource.getIdElement().getIdPart())));
        return resources;
    }
    private List<Encounter> encounters(List<Resource> resources) {
        return resources.stream().filter(Encounter.class::isInstance).map(Encounter.class::cast).toList();
    }
    private boolean level(Encounter encounter, String code) {
        return encounter.getType().stream().flatMap(t -> t.getCoding().stream()).anyMatch(c -> code.equals(c.getCode()));
    }
    private Encounter target(List<Resource> resources, Reference reference) {
        assertTrue(reference.hasReference());
        return encounters(resources).stream().filter(e -> e.getIdElement().getIdPart().equals(reference.getReferenceElement().getIdPart()))
                .findFirst().orElseThrow(() -> new AssertionError("Missing target: " + reference.getReference()));
    }
    @Test public void diagnosisRoleFiltersPreserveConditionsAndUnclassifiedLinksOnlyWhenUnrestricted() throws Exception {
        String[] roles = {"Hauptdiagnose", "Nebendiagnose", "Aufnahmediagnose", ""};
        List<Map<String, String>> rows = new ArrayList<>();
        for (int i = 0; i < roles.length; i++) rows.add(Map.of("Patient-ID", "p1", "Fall-Nr", "1",
                "Code", "A0" + i, "Codesystem", "ICD-10-GM 2026", "Typ", roles[i],
                "Dokumentationszeitpunkt", "2026-01-03"));
        table(TableIdentifier.Diagnose, rows);
        Map<String, Set<String>> scenarios = new LinkedHashMap<>();
        scenarios.put("default", Set.of("A00", "A01", "A02", "A03"));
        scenarios.put("billing,post-op,pre-op,DD,AD,CM,CC", scenarios.get("default"));
        scenarios.put("CC", Set.of("A00"));
        scenarios.put("CM", Set.of("A01"));
        scenarios.put("CC,CM", Set.of("A00", "A01"));
        scenarios.put("AD", Set.of("A02"));
        scenarios.put("", Set.of());
        for (var scenario : scenarios.entrySet()) {
            String settings = "CONTACT_DIAGNOSES_LEVELS=facility,department,ward-service\n";
            if (!scenario.getKey().equals("default")) settings += "CONTACT_DIAGNOSES_ROLES=" + scenario.getKey() + "\n";
            var resources = run(settings);
            Map<String, String> codes = new HashMap<>();
            for (Resource resource : resources) if (resource instanceof Condition) {
                var condition = (Condition)resource;
                codes.put(condition.getIdElement().getIdPart(), condition.getCode().getCodingFirstRep().getCode());
            }
            assertEquals(4, codes.size());
            int carriers = 0;
            for (var encounter : encounters(resources)) {
                Set<String> actual = new HashSet<>();
                for (var reference : encounter.getDiagnosis()) {
                    String code = codes.get(reference.getCondition().getReferenceElement().getIdPart());
                    actual.add(code);
                    if ("A03".equals(code)) assertFalse(reference.hasUse());
                }
                if (!actual.isEmpty()) {
                    assertEquals(scenario.getKey(), scenario.getValue(), actual);
                    assertEquals(actual.size(), encounter.getDiagnosis().size());
                    carriers++;
                }
            }
            assertEquals(scenario.getKey(), scenario.getValue().isEmpty() ? 0 : 3, carriers);
            assertTrue(report.diagnosisReferenceIssues.isEmpty());
        }
    }

    @Test public void diagnosisPropagationAndClinicalReferencesStayWithinPatientAndCaseAtTransferBoundary() throws Exception {
        List<Map<String, String>> diagnoses = new ArrayList<>();
        for (String patient : List.of("p1", "p2")) for (String number : List.of("1", "2"))
            diagnoses.add(Map.of("Patient-ID", patient, "Fall-Nr", number, "Code", "A01", "Codesystem", "ICD-10-GM 2026",
                    "Typ", "Hauptdiagnose", "Dokumentationszeitpunkt", "2026-0" + number + "-05"));
        table(TableIdentifier.Diagnose, diagnoses);
        var resources = run("REFERENCE_CONDITION_ENCOUNTER=department\nCONTACT_DIAGNOSES_LEVELS=department,ward-service\n");
        int checked = 0;
        for (Resource resource : resources) if (resource instanceof Condition) {
            Condition condition = (Condition)resource;
            Encounter department = target(resources, condition.getEncounter());
            assertTrue(level(department, "abteilungskontakt"));
            assertEquals(condition.getSubject().getReference(), department.getSubject().getReference());
            assertEquals(condition.getRecordedDate(), department.getPeriod().getStart());
            Encounter root = target(resources, department.getPartOf());
            assertTrue(condition.getIdElement().getIdPart().startsWith(root.getIdElement().getIdPart()));
            List<Encounter> carriers = encounters(resources).stream().filter(e -> e.getDiagnosis().stream()
                    .anyMatch(d -> condition.getIdElement().getIdPart().equals(d.getCondition().getReferenceElement().getIdPart()))).toList();
            assertEquals(new com.google.gson.Gson().toJson(report.diagnosisReferenceIssues), 2, carriers.size());
            for (Encounter carrier : carriers) {
                assertEquals(condition.getSubject().getReference(), carrier.getSubject().getReference());
                assertEquals(condition.getRecordedDate(), carrier.getPeriod().getStart());
                assertTrue(carrier.getIdElement().getIdPart().startsWith(root.getIdElement().getIdPart()));
            }
            checked++;
        }
        assertEquals(4, checked);
        assertTrue(report.diagnosisReferenceIssues.isEmpty());
        assertTrue(report.encounterReferenceIssues.isEmpty());
    }
    @Test public void disabledDepartmentDoesNotLeaveParentOrClinicalReferencesAndWardCanPointToFacility() throws Exception {
        table(TableIdentifier.Diagnose, List.of(Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Code", "A01",
                "Codesystem", "ICD-10-GM 2026", "Typ", "Hauptdiagnose", "Dokumentationszeitpunkt", "2026-01-05")));
        for (String parent : List.of("department", "facility")) {
            var resources = run("CONTACT_DEPARTMENT_ENABLED=false\nCONTACT_WARD_SERVICE_PART_OF=" + parent
                    + "\nREFERENCE_CONDITION_ENCOUNTER=department\nCONTACT_DIAGNOSES_LEVELS=department,ward-service\n");
            int wards = 0, conditions = 0, diagnoses = 0;
            for (Encounter encounter : encounters(resources)) {
                assertFalse(level(encounter, "abteilungskontakt"));
                diagnoses += encounter.getDiagnosis().size();
                if (level(encounter, "versorgungsstellenkontakt")) {
                    wards++;
                    if (parent.equals("facility")) assertTrue(level(target(resources, encounter.getPartOf()), "einrichtungskontakt"));
                    else assertFalse(encounter.hasPartOf());
                }
            }
            for (Resource resource : resources) if (resource instanceof Condition) {
                assertFalse(((Condition)resource).hasEncounter()); conditions++;
            }
            assertEquals(8, wards); assertEquals(1, conditions); assertEquals(1, diagnoses);
            assertTrue(report.diagnosisReferenceIssues.isEmpty());
        }
    }
    @Test public void procedureDiagnosisReferencesSurviveDefaultAndDownwardAssignmentAndRespectDeselection() throws Exception {
        table(TableIdentifier.Prozedur, List.of(Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Prozedurencode", "123",
                "Codesystem", "SNOMED CT (Version nicht angegeben)", "Durchführungsbeginn", "2026-01-05")));
        for (String selected : List.of("facility", "department", "ward-service")) {
            var resources = run("CONTACT_PROCEDURE_DIAGNOSES_LEVELS=" + selected + "\n");
            var procedures = resources.stream().filter(Procedure.class::isInstance).toList();
            assertEquals(1, procedures.size());
            var carriers = encounters(resources).stream().filter(e -> !e.getDiagnosis().isEmpty()).toList();
            assertEquals(1, carriers.size());
            Encounter carrier = carriers.get(0);
            assertEquals("Procedure/" + procedures.get(0).getIdElement().getIdPart(), carrier.getDiagnosisFirstRep().getCondition().getReference());
            assertTrue(level(carrier, selected.equals("facility") ? "einrichtungskontakt"
                    : selected.equals("department") ? "abteilungskontakt" : "versorgungsstellenkontakt"));
            assertEquals("Patient/p1", carrier.getSubject().getReference());
            assertTrue(report.diagnosisReferenceIssues.isEmpty());
        }
        var disabled = run("PROCEDURE_ENABLED=false\n");
        assertFalse(disabled.stream().anyMatch(Procedure.class::isInstance));
        assertTrue(encounters(disabled).stream().allMatch(e -> e.getDiagnosis().isEmpty()));
    }
    @Test public void documentStrategiesPreserveExplicitConflictsAndOnlyFillTrulyMissingReferences() throws Exception {
        List<Map<String, String>> documents = new ArrayList<>();
        for (String number : List.of("1", "", "missing")) documents.add(Map.of("Patient-ID", "p1", "Fall-Nr", number,
                "Dokumenttext", "Document", "Ausgabezeitpunkt", "2026-02-05T00:00:00Z"));
        documents.add(Map.of("Patient-ID", "p1", "Fall-Nr", "", "Dokumenttext", "Undated document"));
        table(TableIdentifier.DocumentReference, documents);
        for (String strategy : List.of("explicit-only", "fill-missing", "timestamp-only")) {
            var resources = run("REFERENCE_DOCUMENT_REFERENCE_ENCOUNTER=facility\nREFERENCE_DOCUMENT_REFERENCE_ASSIGNMENT_STRATEGY=" + strategy + "\n");
            var docs = resources.stream().filter(DocumentReference.class::isInstance).map(DocumentReference.class::cast).toList();
            assertEquals(4, docs.size());
            for (int i = 0; i < docs.size(); i++) {
                var refs = docs.get(i).getContext().getEncounter();
                boolean assigned = i < 3 && (strategy.equals("timestamp-only") || i == 0 || (i == 1 && strategy.equals("fill-missing")));
                assertEquals(strategy + " document " + i, assigned ? 1 : 0, refs.size());
                if (assigned) {
                    var contact = target(resources, refs.get(0));
                    assertEquals("Patient/p1", contact.getSubject().getReference());
                    assertEquals(i == 0 && !strategy.equals("timestamp-only") ? "2026-01-01" : "2026-02-01",
                            contact.getPeriod().getStartElement().getValueAsString());
                }
            }
            var reasons = report.encounterReferenceIssues.stream().map(issue -> issue.get("reason")).toList();
            if (!strategy.equals("timestamp-only")) {
                assertTrue(reasons.stream().anyMatch(r -> r.startsWith("explicit-contact-time-conflict:")));
                assertTrue(reasons.stream().anyMatch(r -> r.startsWith("unresolved-input-contact:")));
            }
            if (!strategy.equals("explicit-only")) assertTrue(reasons.contains("missing-timestamp"));
        }
    }
    @Test public void reportTimestampCandidatesUseOriginalInputBeforeDarAndEndRules() throws Exception {
        List<Map<String, String>> rows = new ArrayList<>();
        for (String patient : List.of("p1", "p2")) for (String scenario : List.of("first", "fallback", "issued", "missing", "outside")) {
            var row = new LinkedHashMap<>(Map.of("Patient-ID", patient, "Eintrag ID", scenario, "Beschreibung", scenario));
            if (scenario.equals("first")) row.put("Zeitpunkt", "2026-01-05");
            if (scenario.equals("fallback") || scenario.equals("outside")) row.put("Zeitpunkt", "2025-12-01");
            if (!scenario.equals("missing")) row.put("Ausgabezeitpunkt",
                    scenario.equals("outside") ? "2025-12-02T00:00:00Z" : "2026-02-05T00:00:00Z");
            rows.add(row);
        }
        table(TableIdentifier.Befundbericht, rows);
        for (boolean changedOutput : List.of(false, true)) {
            String settings = "REFERENCE_DIAGNOSTIC_REPORT_ENCOUNTER=department\n";
            if (changedOutput) settings += "ENCOUNTER_INPATIENT_END_POLICY=start\nDAR_ENCOUNTER_PERIOD_START=free-contact\n"
                    + "DAR_DIAGNOSTIC_REPORT_EFFECTIVE_X=free-effective\nDAR_DIAGNOSTIC_REPORT_ISSUED=free-issued\n";
            var resources = run(settings);
            var reports = resources.stream().filter(DiagnosticReport.class::isInstance).map(DiagnosticReport.class::cast).toList();
            assertEquals(10, reports.size());
            for (var clinical : reports) {
                String scenario = clinical.getConclusion();
                boolean matches = !List.of("missing", "outside").contains(scenario);
                assertEquals(scenario, matches, clinical.hasEncounter());
                if (matches) {
                    Encounter contact = target(resources, clinical.getEncounter());
                    assertEquals(clinical.getSubject().getReference(), contact.getSubject().getReference());
                    assertTrue(level(contact, "abteilungskontakt"));
                    String date = scenario.equals("first") ? "2026-01-05" : "2026-02-05";
                    assertEquals(date, changedOutput ? contact.getPeriod().getEndElement().getValueAsString()
                            : contact.getPeriod().getStartElement().getValueAsString());
                }
                if (changedOutput) {
                    assertEquals("free-effective", clinical.getEffective().getExtensionByUrl(DarOverrides.URL).getValue().primitiveValue());
                    assertFalse(clinical.getIssuedElement().hasValue());
                    assertEquals("free-issued", clinical.getIssuedElement().getExtensionByUrl(DarOverrides.URL).getValue().primitiveValue());
                }
            }
            assertEquals(4, report.encounterReferenceIssues.size());
            assertEquals(2, report.encounterReferenceIssues.stream().filter(i -> "missing-timestamp".equals(i.get("reason"))).count());
            assertEquals(2, report.encounterReferenceIssues.stream().filter(i -> "no-matching-contact".equals(i.get("reason"))).count());
        }
        var disabled = run("REFERENCE_DIAGNOSTIC_REPORT_ENCOUNTER=none\n");
        assertEquals(10, disabled.stream().filter(DiagnosticReport.class::isInstance).count());
        for (Resource resource : disabled) if (resource instanceof DiagnosticReport) assertFalse(((DiagnosticReport)resource).hasEncounter());
        assertTrue(report.encounterReferenceIssues.isEmpty());
    }

    @Test public void reportPrefersLaterInpatientCandidateAndFallsBackWhenThatClassIsDisabled() throws Exception {
        table(TableIdentifier.Fall, List.of(
                Map.of("Patient-ID", "p1", "Fall-Nr", "1", "Start", "2026-01-01", "Ende", "2026-01-10", "Einrichtungskontaktklasse", "ambulant"),
                Map.of("Patient-ID", "p1", "Fall-Nr", "2", "Start", "2026-02-01", "Ende", "2026-02-10", "Einrichtungskontaktklasse", "stationaer"),
                Map.of("Patient-ID", "p2", "Fall-Nr", "2", "Start", "2026-02-01", "Ende", "2026-02-10", "Einrichtungskontaktklasse", "stationaer")));
        table(TableIdentifier.Befundbericht, List.of(Map.of("Patient-ID", "p1", "Eintrag ID", "report",
                "Zeitpunkt", "2026-01-05", "Ausgabezeitpunkt", "2026-02-05T00:00:00Z")));
        for (boolean inpatient : List.of(true, false)) {
            var resources = run("REFERENCE_DIAGNOSTIC_REPORT_ENCOUNTER=facility\nENCOUNTER_INPATIENT_ENABLED=" + inpatient + "\n");
            var clinical = (DiagnosticReport)resources.stream().filter(DiagnosticReport.class::isInstance).findFirst().orElseThrow();
            Encounter contact = target(resources, clinical.getEncounter());
            assertEquals("Patient/p1", contact.getSubject().getReference());
            assertEquals(inpatient ? "IMP" : "AMB", contact.getClass_().getCode());
            assertEquals(inpatient ? "2026-02-01" : "2026-01-01", contact.getPeriod().getStartElement().getValueAsString());
            assertTrue(report.encounterReferenceIssues.isEmpty());
        }
    }

}
