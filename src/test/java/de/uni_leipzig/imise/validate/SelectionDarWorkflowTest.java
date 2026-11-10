package de.uni_leipzig.imise.validate;

import static org.junit.Assert.*;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Collectors;
import org.junit.*;
import org.junit.rules.TemporaryFolder;
import org.hl7.fhir.r4.model.*;
import de.uni_leipzig.imise.Excel2FhirMain;
import de.uni_leipzig.life.synthifhir.OutputFileType;
import de.uni_leipzig.life.synthifhir.DarOverrides;
import picocli.CommandLine;

/** Real workbook -> converter -> serialized output, including interacting policies. */
public class SelectionDarWorkflowTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();

    @Test public void explicitAndEmptyPatientFieldsSurviveWorkbookConversion() throws Exception {
        Path root = temp.newFolder().toPath(), input = root.resolve("names.xlsx"), output = root.resolve("output");
        try (var stream = Files.newInputStream(Path.of("input/FHIR_Testdatengenerator_Vorlage.xlsx"));
                var book = new org.apache.poi.xssf.usermodel.XSSFWorkbook(stream)) {
            var sheet = book.getSheet("Person");
            var cases = List.of(Map.of("Vorname", "", "Nachname", "", "Geburtsdatum", ""),
                    Map.of("Vorname", "Unbekannt (Data Absent Reason)", "Nachname", "Müller", "Geburtsdatum", "Nicht erhoben (Data Absent Reason)"),
                    Map.of("Vorname", "Anna Maria", "Nachname", "!dar:masked", "Geburtsdatum", "1980-05"));
            for (int i = 0; i < cases.size(); i++) for (var cell : sheet.getRow(0)) {
                String field = cell.getStringCellValue();
                if (cases.get(i).containsKey(field)) sheet.getRow(i + 1).getCell(cell.getColumnIndex(),
                        org.apache.poi.ss.usermodel.Row.MissingCellPolicy.CREATE_NULL_AS_BLANK).setCellValue(cases.get(i).get(field));
            }
            try (var streamOut = Files.newOutputStream(input)) { book.write(streamOut); }
        }
        var checked = new ExcelTemplateValidator().validate(input.toFile());
        assertFalse(checked.getIssues().toString(), checked.hasErrors());
        Path config = root.resolve("input.config");
        Files.writeString(config, "CONFIGURATION_VERSION=1\nOUTPUT_FORMATS=JSON\n");
        assertEquals(0, new CommandLine(new Excel2FhirMain()).execute("-f", input.toString(), "-o", output.toString(),
                "--converter-options", config.toString()));
        var resources = read(output, ".json");
        var patients = resources.values().stream().filter(Patient.class::isInstance).map(Patient.class::cast)
                .collect(Collectors.toMap(p -> p.getIdElement().getIdPart(), p -> p));
        Patient empty = patients.get("Projekt-XY-Patient-00001");
        assertFalse(empty.hasName()); assertFalse(empty.hasBirthDateElement());
        Patient absent = patients.get("Projekt-XY-Patient-00002");
        assertEquals("Müller", absent.getNameFirstRep().getFamily());
        assertEquals("unknown", reason(absent.getNameFirstRep().getGiven().get(0)));
        assertEquals("not-asked", reason(absent.getBirthDateElement()));
        Patient mixed = patients.get("Projekt-XY-Patient-00003");
        assertEquals(List.of("Anna", "Maria"), mixed.getNameFirstRep().getGiven().stream().map(StringType::getValue).toList());
        assertEquals("masked", reason(mixed.getNameFirstRep().getFamilyElement()));
        assertEquals("1980-05", mixed.getBirthDateElement().getValueAsString());
    }

    private Map<String, Resource> convert(String settings) throws Exception {
        Path root = temp.newFolder().toPath(), config = root.resolve("audit.config");
        Files.writeString(config, "CONFIGURATION_VERSION=1\nOUTPUT_FORMATS=JSON,NDJSON\n" + settings);
        Path output = root.resolve("output");
        assertEquals(0, new CommandLine(new Excel2FhirMain()).execute("-f",
                "input/FHIR_Testdatengenerator_Vorlage.xlsx", "-o", output.toString(),
                "--converter-options", config.toString()));
        Map<String, Resource> json = read(output, ".json"), ndjson = read(output, ".ndjson");
        assertEquals(json.keySet(), ndjson.keySet());
        for (String key : json.keySet()) assertTrue("Format mismatch: " + key, json.get(key).equalsDeep(ndjson.get(key)));
        return json;
    }

    @Test public void documentBlocksInStarterWorkbookProduceBothCodingsAndNlpExtension() throws Exception {
        var documents = convert("").values().stream().filter(DocumentReference.class::isInstance)
                .map(DocumentReference.class::cast).toList();
        assertEquals(3, documents.size());
        int embedded = 0, linked = 0;
        for (var document : documents) {
            var attachment = document.getContentFirstRep().getAttachment();
            if (attachment.hasData()) {
                embedded++;
                assertEquals(2, document.getType().getCoding().size());
                assertEquals("Ärztlicher Verlaufsbericht", document.getType().getCodingFirstRep().getDisplay());
                var extension = document.getExtensionByUrl(
                        de.uni_leipzig.life.synthifhir.converter.DocumentReferenceConverter.NLP_EXTENSION);
                assertNotNull(extension);
                assertEquals(List.of("preprocessed", "format-change", "surrogated"),
                        ((CodeableConcept)extension.getValue()).getCoding().stream().map(Coding::getCode).toList());
            } else {
                linked++;
                assertTrue(attachment.hasUrl());
                assertFalse(attachment.hasSize());
                assertFalse(attachment.hasCreation());
            }
        }
        assertEquals(2, embedded);
        assertEquals(1, linked);
    }

    private Map<String, Resource> read(Path root, String extension) throws Exception {
        Map<String, Resource> result = new LinkedHashMap<>();
        try (var files = Files.walk(root)) {
            for (Path file : files.filter(p -> p.toString().contains("/fhir/") && p.toString().endsWith(extension)).toList()) {
                List<String> values = extension.equals(".ndjson") ? Files.readAllLines(file) : List.of(Files.readString(file));
                for (String value : values) {
                    Resource parsed = (Resource)OutputFileType.JSON.getParser().parseResource(value);
                    List<Resource> resources = parsed instanceof Bundle
                            ? ((Bundle)parsed).getEntry().stream().map(Bundle.BundleEntryComponent::getResource).toList() : List.of(parsed);
                    for (Resource resource : resources) {
                        String key = resource.fhirType() + "/" + resource.getIdElement().getIdPart();
                        Resource previous = result.put(key, resource);
                        if (previous != null) assertTrue("Conflicting resource " + key, previous.equalsDeep(resource));
                    }
                }
            }
        }
        assertFalse(result.isEmpty());
        return result;
    }
    private List<Reference> references(Base base) {
        List<Reference> result = new ArrayList<>();
        if (base instanceof Reference) result.add((Reference)base);
        else if (!(base instanceof PrimitiveType<?>)) for (Property property : base.children())
            for (Base child : property.getValues()) result.addAll(references(child));
        return result;
    }
    private Set<String> ids(Map<String, Resource> resources, String type) {
        return resources.keySet().stream().filter(key -> key.startsWith(type + "/")).collect(Collectors.toSet());
    }
    private String reason(Element element) {
        var extension = element.getExtensionByUrl(DarOverrides.URL);
        return extension == null ? null : ((CodeType)extension.getValue()).getValue();
    }
    @Test public void patientMedicationAndLocationModesPreserveIdsAndReferenceIntentWithDar() throws Exception {
        Map<String, Resource> baseline = convert("");
        for (String type : List.of("Patient", "Medication", "Location"))
            assertFalse("Fixture must exercise " + type, ids(baseline, type).isEmpty());
        for (Resource resource : baseline.values()) for (Reference ref : references(resource)) {
            if (!ref.hasReference()) continue;
            String target = ref.getReference();
            if (target.startsWith("Patient/") || target.startsWith("Medication/") || target.startsWith("Location/"))
                assertTrue("Generated target must exist: " + target, baseline.containsKey(target));
        }
        for (String mode : List.of("reference-only", "neither")) {
            var output = convert("PATIENT_MODE=" + mode + "\nMEDICATION_MODE=" + mode + "\nLOCATION_MODE=" + mode
                    + "\nMEDICATION_EXISTING_REFERENCE=true\nLOCATION_EXISTING_REFERENCE=true\n"
                    + "DAR_PATIENT_NAME_FAMILY=made-up\nDAR_ENCOUNTER_PERIOD_END=custom-end\n");
            for (String type : List.of("Patient", "Medication", "Location")) assertTrue(ids(output, type).isEmpty());
            Set<String> expected = new HashSet<>(baseline.keySet());
            expected.removeIf(key -> key.startsWith("Patient/") || key.startsWith("Medication/") || key.startsWith("Location/"));
            assertEquals(expected, output.keySet());
            int externalTargets = 0;
            for (Resource resource : output.values()) {
                for (Reference ref : references(resource)) {
                    if (!ref.hasReference() || ref.getReference().startsWith("#")) continue;
                    String target = ref.getReference();
                    if (target.startsWith("Patient/") || target.startsWith("Medication/") || target.startsWith("Location/")) {
                        assertEquals("neither must remove references: " + target, "reference-only", mode);
                        assertTrue(baseline.containsKey(target)); externalTargets++;
                    }
                }
                if (resource instanceof Encounter) assertEquals("custom-end", reason(((Encounter)resource).getPeriod().getEndElement()));
            }
            if (mode.equals("reference-only")) assertTrue(externalTargets > 0);
        }
    }
    @Test public void deselectedResourcesAreNotRevivedByDarAndTheirLocalReferencesAreRemoved() throws Exception {
        var baseline = convert("");
        List<String> types = List.of("Condition", "Procedure", "Observation", "MedicationRequest", "MedicationAdministration",
                "MedicationStatement", "Immunization", "DiagnosticReport", "CarePlan", "DocumentReference", "Consent");
        String transformations = "MEDICATION_REQUEST_TREATMENT=replace-administration\nMEDICATION_ADMINISTRATION_TREATMENT=add-statement\n";
        var derivedBaseline = convert(transformations);
        var output = convert(transformations + "CONDITION_ENABLED=false\nPROCEDURE_ENABLED=false\nOBSERVATION_LABORATORY_ENABLED=false\n"
                + "OBSERVATION_VITAL_SIGNS_ENABLED=false\nOBSERVATION_OTHER_ENABLED=false\nMEDICATION_REQUEST_ENABLED=false\n"
                + "MEDICATION_ADMINISTRATION_ENABLED=false\nMEDICATION_STATEMENT_ENABLED=false\n"
                + "IMMUNIZATION_ENABLED=false\nDIAGNOSTIC_REPORT_ENABLED=false\nCARE_PLAN_ENABLED=false\n"
                + "DOCUMENT_REFERENCE_ENABLED=false\nCONSENT_ENABLED=false\n"
                + "DAR_LABORATORY_VALUE_X_NUMERIC_MEASUREMENT=made-up\nDAR_PATIENT_NAME_FAMILY=masked\n");
        for (String type : types) {
            if (List.of("Condition", "Procedure", "Observation", "MedicationRequest", "MedicationAdministration", "MedicationStatement").contains(type))
                assertTrue("Fixture must exercise " + type, !ids(baseline, type).isEmpty() || !ids(derivedBaseline, type).isEmpty());
            assertTrue(type, ids(output, type).isEmpty());
        }
        for (Resource resource : output.values()) for (Reference ref : references(resource))
            if (ref.hasReference() && baseline.containsKey(ref.getReference()))
                assertTrue("Dangling local reference " + ref.getReference(), output.containsKey(ref.getReference()));
        for (Resource resource : output.values()) if (resource instanceof Patient)
            assertEquals("masked", reason(((Patient)resource).getNameFirstRep().getFamilyElement()));
    }
    @Test public void customDarPreservesCodingSystemsAndDoesNotInventDerivedDosages() throws Exception {
        String transformations = "MEDICATION_REQUEST_TREATMENT=replace-administration\n";
        var baseline = convert(transformations);
        var output = convert(transformations + "DAR_MEDICATION_ADMINISTRATION_DOSAGE_DOSE=custom-dose\n"
                + "DAR_CONDITION_CODE_CODING_CODE=custom-code\n");
        assertEquals(baseline.keySet(), output.keySet());
        int administrations = 0, codings = 0;
        for (String key : output.keySet()) {
            Resource resource = output.get(key);
            if (resource instanceof MedicationAdministration) {
                var administration = (MedicationAdministration)resource;
                administrations++;
                if (((MedicationAdministration)baseline.get(key)).hasDosage()) {
                    var dose = administration.getDosage().getDose();
                    assertFalse(dose.hasValue()); assertFalse(dose.hasUnit());
                    assertEquals("custom-dose", reason(dose));
                } else {
                    assertFalse("DAR must not invent absent dosage instructions", administration.hasDosage());
                }
            }
            if (resource instanceof Condition) {
                var original = ((Condition)baseline.get(key)).getCode().getCoding();
                var replaced = ((Condition)resource).getCode().getCoding();
                assertEquals(original.size(), replaced.size());
                for (int i = 0; i < original.size(); i++) {
                    assertEquals(original.get(i).getSystem(), replaced.get(i).getSystem());
                    assertFalse(replaced.get(i).getCodeElement().hasValue());
                    assertEquals("custom-code", reason(replaced.get(i).getCodeElement())); codings++;
                }
            }
        }
        assertTrue(administrations > 0); assertTrue(codings > 0);
    }
    @Test public void missingOnlyPreservesValuesAndScopedDarOverridesCommonRuleAfterEndPolicy() throws Exception {
        var baseline = convert("");
        var missing = convert("DAR_PATIENT_NAME_FAMILY=custom-family\nDAR_PATIENT_NAME_FAMILY_ONLY_WHEN_MISSING=true\n"
                + "ENCOUNTER_INPATIENT_END_POLICY=start-plus-second\nDAR_ENCOUNTER_PERIOD_END=common-end\n"
                + "DAR_ENCOUNTER_INPATIENT_PERIOD_END=scoped-end\n");
        var overwrite = convert("DAR_PATIENT_NAME_FAMILY=custom-family\n");
        int namedPatients = 0, inpatient = 0, other = 0;
        for (String key : baseline.keySet()) {
            Resource original = baseline.get(key);
            if (original instanceof Patient) {
                Patient patient = (Patient)original;
                if (patient.getNameFirstRep().hasFamily()) {
                    namedPatients++;
                    assertTrue(patient.getNameFirstRep().getFamilyElement().equalsDeep(((Patient)missing.get(key)).getNameFirstRep().getFamilyElement()));
                }
                var replaced = ((Patient)overwrite.get(key)).getNameFirstRep().getFamilyElement();
                assertFalse(replaced.hasValue()); assertEquals("custom-family", reason(replaced));
            } else if (original instanceof Encounter) {
                Encounter encounter = (Encounter)missing.get(key);
                boolean imp = "IMP".equals(encounter.getClass_().getCode());
                if (imp) inpatient++; else other++;
                assertFalse(encounter.getPeriod().getEndElement().hasValue());
                assertEquals(imp ? "scoped-end" : "common-end", reason(encounter.getPeriod().getEndElement()));
            }
        }
        assertTrue(namedPatients > 0); assertTrue(inpatient > 0); assertTrue(other > 0);
    }
    @Test public void descriptiveReferencesPreserveTargetIdentityAcrossMedicationDerivation() throws Exception {
        String transformations = "MEDICATION_REQUEST_TREATMENT=replace-administration\nMEDICATION_ADMINISTRATION_TREATMENT=add-statement\n";
        var baseline = convert(transformations);
        for (boolean literal : List.of(false, true)) for (boolean description : List.of(false, true)) {
            var output = convert(transformations + "MEDICATION_MODE=reference-only\nLOCATION_MODE=reference-only\n"
                    + "MEDICATION_EXISTING_REFERENCE=" + literal + "\nLOCATION_EXISTING_REFERENCE=" + literal
                    + "\nMEDICATION_DESCRIPTION=" + description + "\nLOCATION_DESCRIPTION=" + description + "\n");
            Set<String> expected = new HashSet<>(baseline.keySet());
            expected.removeIf(key -> key.startsWith("Medication/") || key.startsWith("Location/"));
            assertEquals(expected, output.keySet());
            Set<String> checked = new HashSet<>();
            for (String key : output.keySet()) {
                Resource original = baseline.get(key), changed = output.get(key);
                List<Reference> before = new ArrayList<>(), after = new ArrayList<>();
                if (original instanceof MedicationRequest) {
                    before.add(((MedicationRequest)original).getMedicationReference());
                    if (((MedicationRequest)changed).hasMedication()) after.add(((MedicationRequest)changed).getMedicationReference());
                } else if (original instanceof MedicationAdministration) {
                    before.add(((MedicationAdministration)original).getMedicationReference());
                    if (((MedicationAdministration)changed).hasMedication()) after.add(((MedicationAdministration)changed).getMedicationReference());
                } else if (original instanceof MedicationStatement) {
                    before.add(((MedicationStatement)original).getMedicationReference());
                    if (((MedicationStatement)changed).hasMedication()) after.add(((MedicationStatement)changed).getMedicationReference());
                } else if (original instanceof Encounter) {
                    for (var location : ((Encounter)original).getLocation()) before.add(location.getLocation());
                    for (var location : ((Encounter)changed).getLocation()) after.add(location.getLocation());
                }
                if (before.isEmpty()) continue;
                checked.add(original.fhirType());
                assertEquals(key, literal || description ? before.size() : 0, after.size());
                for (int i = 0; i < after.size(); i++) {
                    Reference ref = after.get(i), source = before.get(i);
                    Resource target = baseline.get(source.getReference());
                    assertNotNull(source.getReference(), target);
                    assertEquals(literal, ref.hasReference());
                    if (literal) assertEquals(source.getReference(), ref.getReference());
                    assertEquals(description, ref.hasIdentifier());
                    if (description) {
                        Identifier identity = target instanceof Medication ? ((Medication)target).getIdentifierFirstRep()
                                : ((Location)target).getIdentifierFirstRep();
                        if (identity.hasValue()) assertTrue(identity.equalsDeep(ref.getIdentifier()));
                        else assertEquals(target.getIdElement().getIdPart(), ref.getIdentifier().getValue());
                        String display = target instanceof Medication ? ((Medication)target).getCode().getText() : ((Location)target).getName();
                        if (display != null) assertEquals(display, ref.getDisplay());
                    } else assertFalse(ref.hasDisplay());
                }
            }
            assertEquals(Set.of("Encounter", "MedicationAdministration", "MedicationStatement"), checked);
        }
    }

    @Test public void scopedMissingOnlyFlagTakesPrecedenceOverCommonFlag() throws Exception {
        // The workbook omits document dates; keep their generated run time out of this contact-policy comparison.
        String shared = "DAR_DOCUMENT_REFERENCE_DATE=audit-date\n";
        var baseline = convert(shared);
        for (boolean scopedMissing : List.of(false, true)) {
            var output = convert(shared + "DAR_ENCOUNTER_PERIOD_START=common-free\nDAR_ENCOUNTER_PERIOD_START_ONLY_WHEN_MISSING=" + !scopedMissing
                    + "\nDAR_ENCOUNTER_INPATIENT_PERIOD_START=scoped-free\nDAR_ENCOUNTER_INPATIENT_PERIOD_START_ONLY_WHEN_MISSING=" + scopedMissing + "\n");
            assertEquals(baseline.keySet(), output.keySet());
            int retained = 0, replaced = 0;
            for (String key : baseline.keySet()) {
                Resource original = baseline.get(key), expected = original.copy();
                if (original instanceof Encounter) {
                    Encounter source = (Encounter)original, actual = (Encounter)output.get(key);
                    assertTrue(source.getPeriod().hasStart());
                    boolean inpatient = "IMP".equals(source.getClass_().getCode());
                    if (inpatient == scopedMissing) retained++;
                    else {
                        assertFalse(actual.getPeriod().getStartElement().hasValue());
                        assertEquals(inpatient ? "scoped-free" : "common-free", reason(actual.getPeriod().getStartElement()));
                        Encounter expectedEncounter = (Encounter)expected;
                        expectedEncounter.getPeriod().setStartElement(actual.getPeriod().getStartElement().copy());
                        // The documented contact policy mirrors its final period to location entries.
                        for (var location : expectedEncounter.getLocation()) location.setPeriod(expectedEncounter.getPeriod().copy());
                        replaced++;
                    }
                }
                assertTrue(key + " scopedMissing=" + scopedMissing, expected.equalsDeep(output.get(key)));
            }
            assertTrue(retained > 0); assertTrue(replaced > 0);
        }
    }

}
