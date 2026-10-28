package de.uni_leipzig.life.synthifhir.converter;

import static org.junit.Assert.*;
import java.io.*;
import java.util.*;
import org.apache.commons.csv.*;
import org.hl7.fhir.r4.model.*;
import org.junit.Test;
import de.uni_leipzig.life.synthifhir.*;

public class ClinicalImportConverterTest {
    @Test public void carePlanRejectsOrphansAndPlanValuesOnContinuationRows() throws Exception {
        var options = new ConverterOptions("");
        var result = new ConverterResult(options);
        assertThrows(IllegalArgumentException.class, () -> new CarePlanConverter(
                row(Map.of("Patient-ID", "", "Aktivitätstext", "Orphan"), CarePlanConverter.Columns.values()),
                "p1", result, null, options).convertInternal());
        for (String field : CarePlanConverter.PLAN_FIELDS) {
            assertFalse(field, CarePlanConverter.continuationErrors(name -> name.equals(field) ? "explicit" : "").isEmpty());
        }
        assertTrue(CarePlanConverter.continuationErrors(name -> name.equals("Aktivitätstext") ? "Activity" : "").isEmpty());
    }

    @Test public void missingEncounterColumnBehavesLikeEmptyColumn() throws Exception {
        var options = new ConverterOptions("");
        for (String csv : List.of("Patient-ID\npatient\n", "Patient-ID,Fall-Nr\npatient,\n")) {
            try (var parser = CSVFormat.DEFAULT.builder().setHeader().setSkipHeaderRecord(true)
                    .setNullString("").get().parse(new StringReader(csv))) {
                var converter = new CarePlanConverter(parser.getRecords().get(0), null,
                        new ConverterResult(options), null, options);
                assertTrue(converter.getEncounterIds().isEmpty());
                CarePlan plan = (CarePlan)converter.convertInternal().get(0);
                assertFalse(plan.hasEncounter());
                assertEquals("Patient/patient", plan.getSubject().getReference());
            }
        }
        var explicit = new CarePlanConverter(row(Map.of("Fall-Nr", "1,2"), CarePlanConverter.Columns.values()),
                null, new ConverterResult(options), null, options);
        assertEquals(List.of("patient-E-1", "patient-E-2"), explicit.getEncounterIds());
    }

    private Patient patientWith(Map<String, String> values) throws Exception {
        var options = new ConverterOptions("");
        return (Patient)new PatientConverter(row(values, PatientConverter.Person_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
    }

    @Test public void emptyPatientNamesStayAbsentAndOrdinaryNamesStayLiteral() throws Exception {
        Patient empty = patientWith(Map.of());
        assertFalse(empty.hasName());
        assertFalse(empty.hasIdentifier());
        assertFalse(empty.hasBirthDateElement());
        for (var values : List.of(Map.of("Vorname", "Anna Maria"), Map.of("Nachname", "Müller"),
                Map.of("Vorname", "Unbekannt", "Nachname", "Maskiert"))) {
            HumanName name = patientWith(values).getNameFirstRep();
            assertEquals(values.get("Nachname"), name.getFamily());
            assertEquals(values.containsKey("Vorname") ? List.of(values.get("Vorname").split(" ")) : List.of(),
                    name.getGiven().stream().map(StringType::getValue).toList());
            assertFalse(name.getFamilyElement().hasExtension());
            assertTrue(name.getGiven().stream().noneMatch(StringType::hasExtension));
        }
    }

    @Test public void explicitPatientNameAndBirthDateDarSurviveJsonAndXml() throws Exception {
        for (String value : List.of("!dar:unknown", "Unbekannt (Data Absent Reason)", "!dar:custom-reason")) {
            Patient patient = patientWith(Map.of("Vorname", value, "Nachname", value, "Geburtsdatum", value));
            String reason = value.equals("!dar:custom-reason") ? "custom-reason" : "unknown";
            for (var format : List.of(OutputFileType.JSON, OutputFileType.XML)) {
                var parser = format.getParser();
                String encoded = parser.encodeResourceToString(patient);
                assertFalse(encoded.contains("DUMMY_"));
                Patient restored = (Patient)parser.parseResource(encoded);
                assertEquals(reason, restored.getNameFirstRep().getFamilyElement().getExtensionFirstRep().getValue().primitiveValue());
                assertEquals(reason, restored.getNameFirstRep().getGiven().get(0).getExtensionFirstRep().getValue().primitiveValue());
                assertEquals(reason, restored.getBirthDateElement().getExtensionFirstRep().getValue().primitiveValue());
                assertFalse(restored.getBirthDateElement().hasValue());
                assertNull(restored.getNameFirstRep().getFamily());
                assertNull(restored.getNameFirstRep().getGiven().get(0).getValue());
            }
        }
        for (String date : List.of("1980", "1980-05", "1980-05-12"))
            assertEquals(date, patientWith(Map.of("Geburtsdatum", date)).getBirthDateElement().getValueAsString());
    }

    @Test public void freeStatusCodesAndDarPassThroughEveryClinicalConverter() throws Exception {
        var options = new ConverterOptions("");
        for (String value : List.of("made-up", "!dar:made-up")) {
            var resources = new ArrayList<Resource>();
            for (String type : MedicationValues.STATUSES.keySet()) {
                var values = new HashMap<>(Map.of("Medikationstyp", type, "Status", value));
                if (type.equals(MedicationValues.REQUEST)) values.put("Absicht", value);
                resources.addAll(new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                        null, new ConverterResult(options), null, options).convertInternal().subList(1, 2));
            }
            var values = Map.of("Eintrag ID", "entry", "Status", value, "Absicht", value);
            resources.addAll(new ClinicalEventConverter.Vaccine(row(values, ClinicalEventConverter.Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new ClinicalEventConverter.Report(row(values, ClinicalEventConverter.Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new CarePlanConverter(row(Map.of("Planstatus", value, "Absicht", value, "Aktivitätsstatus", value), CarePlanConverter.Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new ProcedureConverter(row(values, ProcedureConverter.Procedure_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new DocumentReferenceConverter(row(Map.of("Status", value, "Dokumenttext", "Test"), DocumentReferenceConverter.DocumentReference_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new ObservationLaboratoryConverter(row(values, ObservationLaboratoryConverter.ObservationLaboratory_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new ObservationVitalSignsConverter(row(values, ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new PatientConverter(row(Map.of("Geschlecht", value, "Geburtsdatum", "2000-01-01"), PatientConverter.Person_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
            for (Resource resource : resources) {
                for (OutputFileType format : List.of(OutputFileType.JSON, OutputFileType.XML)) {
                    String encoded = format.getParser().encodeResourceToString(resource.copy());
                    assertTrue(resource.fhirType() + ": " + encoded, encoded.contains("made-up"));
                    if (value.startsWith("!dar:")) assertTrue(encoded.contains("data-absent-reason"));
                }
            }
        }
    }

    @Test public void unitWithoutDoseAndAtcVersionWithoutCodeArePreserved() throws Exception {
        for (String type : List.of("Verordnung", "Verabreichung", "Medikationsaussage")) {
            for (String version : List.of("2026", "!dar:unknown")) {
                var values = new HashMap<>(Map.of("Medikationstyp", type, "ATC-Version", version,
                        "Dosiereinheit", "mg", "Dosierungstext", "Supplied text"));
                assertTrue(MedicationValues.errors(values::get).toString(), MedicationValues.errors(values::get).isEmpty());
                var options = new ConverterOptions("");
                var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                        null, new ConverterResult(options), null, options).convertInternal();
                var parser = ca.uhn.fhir.context.FhirContext.forR4Cached().newJsonParser();
                var medication = (Medication)parser.parseResource(parser.encodeResourceToString(resources.get(0)));
                var coding = medication.getCode().getCodingFirstRep();
                assertEquals("http://fhir.de/CodeSystem/bfarm/atc", coding.getSystem());
                assertFalse(coding.hasCodeElement());
                if (version.equals("2026")) assertEquals("2026", coding.getVersion());
                else assertEquals("unknown", coding.getVersionElement().getExtensionFirstRep().getValue().primitiveValue());
                var event = parser.parseResource(parser.encodeResourceToString(resources.get(1)));
                Quantity dose;
                if (event instanceof MedicationAdministration) {
                    dose = ((MedicationAdministration)event).getDosage().getDose();
                    assertEquals("Supplied text", ((MedicationAdministration)event).getDosage().getText());
                } else {
                    var dosage = event instanceof MedicationRequest ? ((MedicationRequest)event).getDosageInstructionFirstRep()
                            : ((MedicationStatement)event).getDosageFirstRep();
                    dose = dosage.getDoseAndRateFirstRep().getDoseQuantity();
                    assertEquals("Supplied text", dosage.getText());
                }
                assertFalse(dose.hasValueElement());
                assertEquals("mg", dose.getCode());
                assertFalse(dose.hasSystemElement());
            }
        }
    }

    @Test public void medicationDoseSystemsAndTextSurviveJsonForAllEventTypes() throws Exception {
        for (String type : List.of("Verordnung", "Verabreichung", "Medikationsaussage")) {
            for (String system : List.of("", "http://unitsofmeasure.org", "https://example.org/units", "UCUM", "eigenes System")) {
                var values = new HashMap<>(Map.of("Medikationstyp", type, "Einzeldosis", "2",
                        "Dosiereinheit", "µg", "Dosiereinheitcodesystem", system,
                        "Dosierungstext", "Nach dem Essen"));
                var options = new ConverterOptions("");
                assertTrue(MedicationValues.errors(values::get).isEmpty());
                var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                        null, new ConverterResult(options), null, options).convertInternal();
                var parser = ca.uhn.fhir.context.FhirContext.forR4Cached().newJsonParser();
                var event = parser.parseResource(parser.encodeResourceToString(resources.get(1)));
                Quantity dose;
                String text;
                if (event instanceof MedicationAdministration) {
                    dose = ((MedicationAdministration)event).getDosage().getDose();
                    text = ((MedicationAdministration)event).getDosage().getText();
                } else {
                    Dosage dosage = event instanceof MedicationRequest ? ((MedicationRequest)event).getDosageInstructionFirstRep()
                            : ((MedicationStatement)event).getDosageFirstRep();
                    dose = dosage.getDoseAndRateFirstRep().getDoseQuantity();
                    text = dosage.getText();
                    assertFalse(dosage.hasTiming());
                }
                assertEquals(!system.isEmpty(), dose.hasSystemElement());
                if (!system.isEmpty()) assertEquals(system, dose.getSystem());
                assertEquals("2", dose.getValue().toPlainString());
                assertEquals("µg", dose.getCode());
                assertTrue(text.contains("Nach dem Essen"));
            }
        }
    }

    @Test public void medicationTimingAndPartialCustomDoseRemainIndependent() throws Exception {
        var options = new ConverterOptions("");
        for (String type : List.of("Verordnung", "Medikationsaussage")) {
            var values = new HashMap<>(Map.of("Medikationstyp", type, "Dosen pro Tag", "3", "Dosierungstext", "Nach dem Essen"));
            for (boolean customDose : List.of(false, true)) {
                if (customDose) {
                    values.put("Dosiereinheitcodesystem", "mein-system");
                    values.put("Einzeldosis", "!dar:unknown");
                }
                var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                        null, new ConverterResult(options), null, options).convertInternal();
                Resource event = resources.get(1);
                Dosage dosage = event instanceof MedicationRequest ? ((MedicationRequest)event).getDosageInstructionFirstRep()
                        : ((MedicationStatement)event).getDosageFirstRep();
                assertEquals(3, dosage.getTiming().getRepeat().getFrequency());
                assertEquals(customDose, dosage.hasDoseAndRate());
                if (customDose) {
                    Quantity dose = dosage.getDoseAndRateFirstRep().getDoseQuantity();
                    assertEquals("mein-system", dose.getSystem());
                    assertFalse(dose.hasCodeElement());
                    assertFalse(dose.hasUnitElement());
                    assertEquals("unknown", dose.getValueElement().getExtensionFirstRep().getValue().primitiveValue());
                }
            }
        }
    }

    @Test public void duplicateDiagnosisSystemsArePreserved() throws Exception {
        var options = new ConverterOptions("");
        var condition = (Condition)new ConditionConverter(row(Map.of("Code", "A01", "Codesystem", "ICD-10-GM 2026",
                "Zusatzcode", "A02", "Zusatzcodesystem", "ICD-10-GM 2026"), ConditionConverter.Diagnosis_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals(2, condition.getCode().getCoding().size());
        assertEquals("A01", condition.getCode().getCoding().get(0).getCode());
        assertEquals("A02", condition.getCode().getCoding().get(1).getCode());
    }
    @Test public void medicationProfileViolationsAreEmittedWithoutRepair() throws Exception {
        var options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Medikationstyp", "Verabreichung", "Präparatcode", "bad-pzn",
                "Präparatcodesystem", "PZN", "Wirkstoffcode", "bad-unii", "Wirkstoffcodesystem", "UNII",
                "Dosierungstext", "Text without dose"));
        assertTrue(MedicationValues.errors(values::get).toString(), MedicationValues.errors(values::get).isEmpty());
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        var drug = (Medication)resources.get(0);
        assertEquals("bad-pzn", drug.getCode().getCodingFirstRep().getCode());
        assertEquals("bad-unii", drug.getIngredientFirstRep().getItemCodeableConcept().getCodingFirstRep().getCode());
        var administration = (MedicationAdministration)resources.get(1);
        assertFalse(administration.hasEffective());
        assertFalse(administration.getDosage().hasDose());
        assertEquals("Text without dose", administration.getDosage().getText());
        values.put("Einzeldosis", "-2");
        resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        assertEquals("-2", ((MedicationAdministration)resources.get(1)).getDosage().getDose().getValue().toPlainString());
        values.remove("Wirkstoffcode"); values.remove("Wirkstoffcodesystem");
        resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        assertFalse(((Medication)resources.get(0)).hasIngredient());
    }
    @Test public void medicationPeriodMayEndBeforeItStarts() throws Exception {
        var values = new HashMap<>(Map.of("Medikationstyp", "Verabreichung", "Präparatbezeichnung", "Test",
                "Wirkstoffcode", "Unbekannt", "Wirkstoffcodesystem", "UNII",
                "Beginn", "2026-05-05", "Ende", "2026-05-01"));
        assertTrue(MedicationValues.errors(values::get).toString(), MedicationValues.errors(values::get).isEmpty());
        var options = new ConverterOptions("");
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        var period = ((MedicationAdministration)resources.get(1)).getEffectivePeriod();
        assertEquals("2026-05-05", period.getStartElement().getValueAsString());
        assertEquals("2026-05-01", period.getEndElement().getValueAsString());
    }

    @Test public void inputContextTracksExplicitDocumentTimeAndLeavesEmptyTimeAbsent() throws Exception {
        var options = ConverterOptions.fromText("PID_PREFIX=prefix-\n");
        var result = new ConverterResult(options);
        for (boolean explicit : List.of(false, true)) {
            var values = new HashMap<>(Map.of("Dokumenttext", "Example document"));
            if (explicit) values.put("Ausgabezeitpunkt", "2026-05-02T00:00:00Z");
            var resource = new DocumentReferenceConverter(row(values, DocumentReferenceConverter.DocumentReference_Columns.values()),
                    null, result, null, options).convert().get(0);
            assertEquals(explicit, ((DocumentReference)resource).hasDate());
            var context = result.inputContext(resource);
            assertEquals("prefix-patient", context.patientId());
            assertEquals(explicit, context.explicitDocumentTimestamp());
            assertEquals(1, context.encounterIds().size());
        }
    }
    @Test public void annualVersionInputsSerializeTextAbsenceAndOmissionForEveryAnnualResource() throws Exception {
        var options = new ConverterOptions("");
        var parser = ca.uhn.fhir.context.FhirContext.forR4Cached().newJsonParser();
        var inputs = new ArrayList<>(List.of("2025", "2026", "2015", "custom edition", " release #1 ", ""));
        inputs.addAll(DiagnosisValues.ABSENT_LABELS.keySet());
        var ids = new HashSet<String>();
        for (String version : inputs) {
            var medicationValues = new HashMap<>(Map.of("Medikationstyp", "Verordnung", "ATC-Code", "A10BA02",
                    "ATC-Version", version, "Präparatbezeichnung", "Test", "Wirkstoffcode", "!dar:unknown", "Wirkstoffcodesystem", "UNII"));
            assertTrue(MedicationValues.errors(medicationValues::get).toString(), MedicationValues.errors(medicationValues::get).isEmpty());
            Medication medication = (Medication) new MedicationConverter(row(medicationValues, MedicationConverter.Medication_Columns.values()),
                    null, new ConverterResult(options), null, options).convertInternal().get(0);
            ids.add(medication.getId());
            Condition condition = (Condition) new ConditionConverter(row(Map.of("Code", "R53", "Codesystem", "ICD-10-GM",
                    "Version", version), ConditionConverter.Diagnosis_Columns.values()), null, new ConverterResult(options), null, options).convertInternal().get(0);
            Procedure procedure = (Procedure) new ProcedureConverter(row(Map.of("Prozedurencode", "5-511.y", "Codesystem", "OPS",
                    "Version", version), ProcedureConverter.Procedure_Columns.values()), null, new ConverterResult(options), null, options).convertInternal().get(0);
            assertFalse("Empty category remains absent regardless of OPS version", procedure.hasCategory());
            Immunization vaccine = (Immunization) new ClinicalEventConverter.Vaccine(row(Map.of("Eintrag ID", "v", "Code", "J07BF03",
                    "Codesystem", "ATC", "Version", version), ClinicalEventConverter.Columns.values()), null, new ConverterResult(options), null, options).convertInternal().get(0);
            for (Coding coding : List.of(medication.getCode().getCodingFirstRep(), condition.getCode().getCodingFirstRep(),
                    procedure.getCode().getCodingFirstRep(), vaccine.getVaccineCode().getCodingFirstRep())) {
                var json = new com.fasterxml.jackson.databind.ObjectMapper().readTree(parser.encodeResourceToString(
                        new Observation().setCode(new CodeableConcept().addCoding(coding)))).path("code").path("coding").get(0);
                var absent = DiagnosisValues.absentReason(version);
                if (absent != null) {
                    assertFalse(json.has("version"));
                    assertEquals(absent.getValue().primitiveValue(), json.path("_version").path("extension").get(0).path("valueCode").asText());
                } else if (version.isEmpty()) {
                    assertFalse(json.has("version")); assertFalse(json.has("_version"));
                } else {
                    assertEquals(version, json.path("version").asText()); assertFalse(json.has("_version"));
                }
            }
        }
        assertTrue("Different version representations must not merge medications", ids.size() >= 20);
    }

    @Test public void germanMissingLabelsPreserveFhirMeaningAndLegacyInputs() throws Exception {
        for (var entry : DiagnosisValues.ABSENT_LABELS.entrySet()) {
            assertTrue(DiagnosisValues.absentReason(entry.getKey()).equalsDeep(
                    DiagnosisValues.absentReason("!dar:" + entry.getValue())));
        }
        assertNull(AdmissionReasonValues.extension(""));
        var reason = (Coding) AdmissionReasonValues.extension("Unbekannt (Data Absent Reason)").getExtensionFirstRep().getValue();
        assertNull(reason.getCode());
        assertEquals("unknown", reason.getCodeElement().getExtensionFirstRep().getValue().primitiveValue());
        assertEquals("7", ((Coding) AdmissionReasonValues.extension("Notfall").getExtensionFirstRep().getValue()).getCode());
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Medikationstyp", "Verabreichung", "Präparatbezeichnung", "Testpräparat",
                "Wirkstoffcode", "Unbekannt (Data Absent Reason)", "Wirkstoffcodesystem", "UNII", "Beginn", "Noch nicht bekannt (Data Absent Reason)",
                "Einzeldosis", "Unbekannt (Data Absent Reason)", "Dosierungstext", "Dosis noch nicht dokumentiert"));
        assertTrue(MedicationValues.errors(values::get).toString(), MedicationValues.errors(values::get).isEmpty());
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        var administration = (MedicationAdministration) resources.get(1);
        assertEquals("unknown", administration.getDosage().getDose().getValueElement()
                .getExtensionFirstRep().getValue().primitiveValue());
        assertEquals("temp-unknown", administration.getEffectiveDateTimeType().getExtensionFirstRep().getValue().primitiveValue());
        var ingredient = ((Medication) resources.get(0)).getIngredientFirstRep().getItemCodeableConcept().getCodingFirstRep();
        assertNull(ingredient.getCode());
        assertEquals("unknown", ingredient.getCodeElement().getExtensionFirstRep().getValue().primitiveValue());
        values.put("Wirkstoffcode", "R16CO5Y76E; Unbekannt");
        assertTrue(MedicationValues.errors(values::get).isEmpty());
    }

    @Test public void combinationIngredientsBecomeSeparateUniiIngredients() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Medikationstyp", "Verordnung", "Präparatbezeichnung", "Kombination",
                "Wirkstoffcode", "R16CO5Y76E; WK2XYI10QM", "Wirkstoffcodesystem", "UNII"));
        assertTrue(MedicationValues.errors(values::get).isEmpty());
        Medication medication = (Medication)new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals(2, medication.getIngredient().size());
        assertEquals("http://fdasis.nlm.nih.gov", medication.getIngredient().get(0).getItemCodeableConcept().getCodingFirstRep().getSystem());
        assertEquals("R16CO5Y76E", medication.getIngredient().get(0).getItemCodeableConcept().getCodingFirstRep().getCode());
        assertEquals("WK2XYI10QM", medication.getIngredient().get(1).getItemCodeableConcept().getCodingFirstRep().getCode());
        values.put("Wirkstoffcode", "R16CO5Y76E;");
        assertFalse(MedicationValues.errors(values::get).isEmpty());
        for (String input : List.of("R16CO5Y76E; R16CO5Y76E", "invalid", "R16CO5Y76E; !dar:unknown")) {
            values.put("Wirkstoffcode", input);
            assertTrue(input, MedicationValues.errors(values::get).isEmpty());
            var out = (Medication)new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                    null, new ConverterResult(options), null, options).convertInternal().get(0);
            assertEquals(input.split(";").length, out.getIngredient().size());
        }
    }
    @Test public void medicationOrdersGermanCodingFirstAndPreservesUnmappedProducts() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Medikationstyp", "Verordnung", "Präparatbezeichnung", "Präparat A",
                "Präparatcode", "123", "Präparatcodesystem", DiagnosisValues.SNOMED,
                "ATC-Code", "M01AE01", "ATC-Version", "2026",
                "Wirkstoffcode", "!dar:unknown", "Wirkstoffcodesystem", DiagnosisValues.SNOMED));
        var columns = MedicationConverter.Medication_Columns.values();
        Medication medication = (Medication)new MedicationConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals("http://fhir.de/CodeSystem/bfarm/atc", medication.getCode().getCodingFirstRep().getSystem());
        assertEquals("2026", medication.getCode().getCodingFirstRep().getVersion());
        assertEquals("http://snomed.info/sct", medication.getCode().getCoding().get(1).getSystem());
        values.remove("Präparatcode"); values.remove("Präparatcodesystem");
        values.remove("ATC-Code"); values.remove("ATC-Version");
        Medication unmapped = (Medication)new MedicationConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals("Präparat A", unmapped.getCode().getText());
        assertFalse(unmapped.getCode().hasCoding());
        values.put("Präparatbezeichnung", "Präparat B");
        Medication other = (Medication)new MedicationConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        assertNotEquals(unmapped.getId(), other.getId());
    }
    @Test public void additionalObservationCodingDoesNotBecomeAResultOrAnotherObservation() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Untersuchungscode", "72166-2", "Codesystem", "LOINC",
                "Zusatzcode", "365981007", "Zusatzcodesystem", DiagnosisValues.SNOMED,
                "Werttyp", "Code", "Wertcode", "266919005", "Wertcodesystem", DiagnosisValues.SNOMED,
                "Wert", "Nie geraucht", "Kategorie", "social-history"));
        var converted = new ObservationVitalSignsConverter(row(values,
                ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        assertEquals(1, converted.size());
        Observation observation = (Observation)converted.get(0);
        assertEquals(2, observation.getCode().getCoding().size());
        assertEquals("72166-2", observation.getCode().getCodingFirstRep().getCode());
        assertEquals("365981007", observation.getCode().getCoding().get(1).getCode());
        assertEquals(1, observation.getValueCodeableConcept().getCoding().size());
        assertEquals("266919005", observation.getValueCodeableConcept().getCodingFirstRep().getCode());
    }
    @Test public void laboratoryKeepsOnlyTheEnteredCategoryCoding() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        Observation observation = (Observation)new ObservationLaboratoryConverter(
                row(Map.of("LOINC", "718-7", "Messwert", "14", "Einheit", "g/dL", "Kategorie", "laboratory"),
                        ObservationLaboratoryConverter.ObservationLaboratory_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertTrue(observation.getCategoryFirstRep().hasCoding(
                "http://terminology.hl7.org/CodeSystem/observation-category", "laboratory"));
        assertFalse(observation.getCategoryFirstRep().hasCoding("http://loinc.org", "26436-6"));
        assertEquals(1, observation.getCategoryFirstRep().getCoding().size());
    }
    private CSVRecord row(Map<String,String> values, Enum<?>[] columns) throws Exception {
        Map<String,String> data = new LinkedHashMap<>();
        data.put("Patient-ID", "patient"); data.put("Fall-Nr", "1");
        for (Enum<?> c : columns) data.put(c.toString(), "");
        data.putAll(values);
        StringWriter out = new StringWriter();
        try (CSVPrinter p = new CSVPrinter(out, CSVFormat.DEFAULT)) { p.printRecord(data.keySet()); p.printRecord(data.values()); }
        try (CSVParser p = CSVFormat.DEFAULT.builder().setHeader().setSkipHeaderRecord(true).setNullString("").get().parse(new StringReader(out.toString()))) {
            return p.getRecords().get(0);
        }
    }
    @Test public void measurementQuantityFieldsRemainIndependentAndComparatorsWorkWithAnySystem() throws Exception {
        var options = new ConverterOptions("");
        for (boolean clinical : List.of(false, true)) {
            for (String system : List.of("", "http://unitsofmeasure.org", "my units")) {
                for (String code : List.of("", "µg")) {
                    for (String raw : List.of("", "5", "<5", "<=5", ">5", ">=5")) {
                        var values = new HashMap<>(Map.of("Werttyp", "Zahl", "Messwert", raw, "Wert", raw,
                                "Einheit", "freie Einheit", "Einheitencode", code, "Einheitencodesystem", system));
                        var result = new ConverterResult(options);
                        var columns = clinical ? ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()
                                : ObservationLaboratoryConverter.ObservationLaboratory_Columns.values();
                        ObservationLaboratoryConverter converter = clinical
                                ? new ObservationVitalSignsConverter(row(values, columns), null, result, null, options)
                                : new ObservationLaboratoryConverter(row(values, columns), null, result, null, options);
                        Observation observation = (Observation)converter.convertInternal().get(0);
                        var parser = ca.uhn.fhir.context.FhirContext.forR4Cached().newJsonParser();
                        observation = (Observation)parser.parseResource(parser.encodeResourceToString(observation));
                        Quantity quantity = observation.getValueQuantity();
                        assertEquals("freie Einheit", quantity.getUnit());
                        assertEquals(!code.isEmpty(), quantity.hasCodeElement());
                        if (!code.isEmpty()) assertEquals(code, quantity.getCode());
                        assertEquals(!system.isEmpty(), quantity.hasSystemElement());
                        if (!system.isEmpty()) assertEquals(system, quantity.getSystem());
                        assertEquals(!raw.isEmpty(), quantity.hasValueElement());
                        if (!raw.isEmpty()) assertEquals("5", quantity.getValue().toPlainString());
                        String comparator = raw.replace("5", "");
                        assertEquals(!comparator.isEmpty(), quantity.hasComparator());
                        if (!comparator.isEmpty()) assertEquals(comparator, quantity.getComparator().toCode());
                    }
                }
            }
        }
    }

    @Test public void numericMeasurementsDoNotInventUnitCodeSystemOrMissingReasons() throws Exception {
        var options = new ConverterOptions("");
        for (boolean clinical : List.of(false, true)) {
            var values = Map.of("Werttyp", "Zahl", "Messwert", "0", "Wert", "0");
            var columns = clinical ? ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()
                    : ObservationLaboratoryConverter.ObservationLaboratory_Columns.values();
            ObservationLaboratoryConverter converter = clinical
                    ? new ObservationVitalSignsConverter(row(values, columns), null, new ConverterResult(options), null, options)
                    : new ObservationLaboratoryConverter(row(values, columns), null, new ConverterResult(options), null, options);
            Quantity quantity = ((Observation)converter.convertInternal().get(0)).getValueQuantity();
            assertEquals("0", quantity.getValue().toPlainString());
            assertFalse(quantity.hasSystemElement());
            assertFalse(quantity.hasCodeElement());
            assertFalse(quantity.hasUnitElement());
        }
    }
    @Test public void procedurePreservesOriginalCodingPeriodAndStatus() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var converter = new ProcedureConverter(row(Map.of("Prozedurencode", "00123", "Codesystem", DiagnosisValues.SNOMED,
                "Durchführungsbeginn", "2026-01-02T10:00:00Z", "Ende", "2026-01-02T10:30:00Z", "Status", "in-progress"),
                ProcedureConverter.Procedure_Columns.values()), null, new ConverterResult(options), null, options);
        Procedure p = (Procedure)converter.convertInternal().get(0);
        assertEquals("00123", p.getCode().getCodingFirstRep().getCode());
        assertEquals("http://snomed.info/sct", p.getCode().getCodingFirstRep().getSystem());
        assertEquals(Procedure.ProcedureStatus.INPROGRESS, p.getStatus());
        assertTrue(p.getPerformedPeriod().hasEnd()); assertFalse(p.hasCategory());
    }
    @Test public void componentsAttachToParentAndVitalSignsAreNotLaboratory() throws Exception {
        ConverterOptions options = new ConverterOptions(""); ConverterResult result = new ConverterResult(options);
        var columns = ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values();
        var parent = new ObservationVitalSignsConverter(row(Map.of("Untersuchungscode", "85354-9", "Untersuchung ID", "bp",
                "Werttyp", "Komponenten", "Zeitstempel", "2026-01-02", "Kategorie", "vital-signs"), columns), null, result, null, options);
        Observation o = (Observation)parent.convertInternal().get(0);
        result.add(TableIdentifier.Klinische_Dokumentation, o);
        var component = new ObservationVitalSignsConverter(row(Map.of("Untersuchungscode", "8480-6", "Komponente von", "bp",
                "Werttyp", "Zahl", "Wert", "<120", "Einheit", "mmHg", "Einheitencode", "mm[Hg]",
                "Einheitencodesystem", "custom-system"), columns), null, result, null, options);
        assertTrue(component.convertInternal().isEmpty());
        assertEquals(1, o.getComponent().size());
        assertEquals("120", o.getComponentFirstRep().getValueQuantity().getValue().toPlainString());
        assertEquals("custom-system", o.getComponentFirstRep().getValueQuantity().getSystem());
        assertEquals(Quantity.QuantityComparator.LESS_THAN, o.getComponentFirstRep().getValueQuantity().getComparator());
        assertEquals("vital-signs", o.getCategoryFirstRep().getCodingFirstRep().getCode());
        assertFalse(o.hasMeta()); assertTrue(o.getId().length() <= 64);
        var orphan = new ObservationVitalSignsConverter(row(Map.of("Untersuchungscode", "8480-6", "Komponente von", "missing"), columns), null, result, null, options);
        assertThrows(IllegalArgumentException.class, () -> orphan.convertInternal());
    }
    @Test public void medicationKeepsDistinctProductsAndDoesNotTurnDoseFrequencyIntoDose() throws Exception {
        ConverterOptions options = new ConverterOptions(""); ConverterResult result = new ConverterResult(options);
        var values = new HashMap<>(Map.of("Präparatcode", "123", "Präparatcodesystem", "RxNorm", "Medikationstyp", "Verordnung",
                "Dokumentationszeitpunkt", "2026-01-02", "Einzeldosis", "2", "Dosiereinheit", "mg", "Dosen pro Tag", "3",
                "Wirkstoffcode", "!dar:unknown", "Wirkstoffcodesystem", DiagnosisValues.SNOMED));
        var converter = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null, result, null, options);
        var first = converter.convertInternal(); Medication medication = (Medication)first.get(0);
        MedicationRequest request = (MedicationRequest)first.get(1);
        assertEquals("123", medication.getCode().getCodingFirstRep().getCode());
        assertEquals("2", request.getDosageInstructionFirstRep().getDoseAndRateFirstRep().getDoseQuantity().getValue().toPlainString());
        assertEquals(3, request.getDosageInstructionFirstRep().getTiming().getRepeat().getFrequency());
        assertFalse(medication.getIngredientFirstRep().hasStrength());
        values.put("Präparatcode", "456");
        var second = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null, result, null, options).convertInternal();
        assertNotEquals(medication.getId(), second.get(0).getId());
    }
    @Test public void localProductPznPreservesLeadingZerosAndDoseSemantics() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Präparatcode", "00000000", "Präparatcodesystem", "PZN",
                "Medikationstyp", "Verordnung", "Dokumentationszeitpunkt", "2026-01-02",
                "Einzeldosis", "2", "Dosiereinheit", "mg", "Dosen pro Tag", "3",
                "Wirkstoffcode", "!dar:unknown", "Wirkstoffcodesystem", DiagnosisValues.SNOMED));
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        Medication medication = (Medication) resources.get(0);
        MedicationRequest request = (MedicationRequest) resources.get(1);
        assertEquals("http://fhir.de/CodeSystem/ifa/pzn", medication.getCode().getCodingFirstRep().getSystem());
        assertEquals("00000000", medication.getCode().getCodingFirstRep().getCode());
        assertFalse(medication.getIngredientFirstRep().hasStrength());
        assertEquals("2", request.getDosageInstructionFirstRep().getDoseAndRateFirstRep().getDoseQuantity().getValue().toPlainString());
        assertEquals(3, request.getDosageInstructionFirstRep().getTiming().getRepeat().getFrequency());
    }
    @Test public void textLabResultPreservesTextWithoutInventedMissingCoding() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        Observation observation = (Observation)new ObservationLaboratoryConverter(
                row(Map.of("LOINC", "630-4", "Messwert", "Escherichia coli nachgewiesen", "Werttyp", "Text"),
                        ObservationLaboratoryConverter.ObservationLaboratory_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals("Escherichia coli nachgewiesen", observation.getValueStringType().getValue());
        assertFalse(OutputFileType.JSON.getParser().encodeResourceToString(observation).contains("data-absent-reason"));
    }

    @Test public void partialAndMixedDosagesPreserveFactsWithoutInventedTiming() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Präparatcode", "123", "Präparatcodesystem", "RxNorm",
                "Medikationstyp", "Verordnung", "Dokumentationszeitpunkt", "2026-01-02", "Einzeldosis", "2"));
        values.put("Wirkstoffcode", "!dar:unknown");
        values.put("Wirkstoffcodesystem", DiagnosisValues.SNOMED);
        var columns = MedicationConverter.Medication_Columns.values();
        MedicationRequest request = (MedicationRequest)new MedicationConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(1);
        Dosage dosage = request.getDosageInstructionFirstRep();
        assertEquals("Einzeldosis: 2 (Einheit unbekannt)", dosage.getText());
        assertFalse(dosage.hasTiming());
        assertEquals("2", dosage.getDoseAndRateFirstRep().getDoseQuantity().getValue().toPlainString());
        values.put("Dosierungstext", "Nach dem Essen");
        values.put("Dosen pro Tag", "3");
        values.put("Dosiereinheit", "mg");
        request = (MedicationRequest)new MedicationConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(1);
        dosage = request.getDosageInstructionFirstRep();
        assertEquals("Nach dem Essen; Einzeldosis: 2 mg; Dosen pro Tag: 3", dosage.getText());
        assertEquals(3, dosage.getTiming().getRepeat().getFrequency());
        assertEquals("2", dosage.getDoseAndRateFirstRep().getDoseQuantity().getValue().toPlainString());
    }

    @Test public void germanAddressNameBecomesIsoCodeWithoutChangingWorkbookConvention() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        Patient patient = (Patient)new PatientConverter(row(Map.of("Land", "DE", "Bundesland", "Hamburg",
                "Geburtsdatum", "2000-01-01"), PatientConverter.Person_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals("DE-HH", patient.getAddressFirstRep().getState());
    }

    @Test public void insurerIsNeverEmittedAsGeneralPractitioner() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        Patient patient = (Patient)new PatientConverter(row(Map.of("Krankenkasse", "AOK",
                "Geburtsdatum", "2000-01-01"), PatientConverter.Person_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        assertFalse(patient.hasGeneralPractitioner());
    }

    @Test public void structuredAddressSurvivesMissingCountryAndEmptyAddressIsOmitted() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Straße", "Musterstraße 7", "Postleitzahl", "01234",
                "Ort", "Beispielort", "Geburtsdatum", "2000-01-01"));
        var columns = PatientConverter.Person_Columns.values();
        Patient patient = (Patient)new PatientConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        Address address = patient.getAddressFirstRep();
        assertEquals("Musterstraße 7", address.getLine().get(0).getValue());
        assertEquals("01234", address.getPostalCode());
        assertEquals("Beispielort", address.getCity());
        assertFalse(address.hasCountry());
        assertFalse(address.hasExtension());
        values.keySet().removeAll(List.of("Straße", "Postleitzahl", "Ort"));
        patient = (Patient)new PatientConverter(row(values, columns), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        assertFalse(patient.hasAddress());
    }

    @Test public void medicationTimeMatrixRejectsMisplacedDatesAndUnknownTypes() throws Exception {
        var values = new HashMap<>(Map.of("Medikationstyp", "Verordnung", "Präparatcode", "123", "Präparatcodesystem", "RxNorm",
                "Wirkstoffcode", "!dar:unknown", "Wirkstoffcodesystem", DiagnosisValues.SNOMED));
        assertTrue(MedicationValues.errors(values::get).isEmpty());
        values.put("Beginn", "2026-01-01");
        assertFalse(MedicationValues.errors(values::get).isEmpty());
        values.put("Medikationstyp", "Verabreichung");
        values.put("Status", "draft");
        assertTrue(MedicationValues.errors(values::get).isEmpty());
        values.put("Status", "completed");
        values.put("Ende", "2025-12-31");
        assertTrue(MedicationValues.errors(values::get).isEmpty());
        values.remove("Ende"); values.put("Beginn", "!dar:unknown");
        assertTrue(MedicationValues.errors(values::get).isEmpty());
        ConverterOptions options = new ConverterOptions("");
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        MedicationAdministration administration = (MedicationAdministration)resources.get(1);
        assertEquals("unknown", administration.getEffectiveDateTimeType().getExtensionFirstRep().getValue().primitiveValue());
        values.put("Medikationstyp", "Vertippt");
        assertThrows(IllegalArgumentException.class, () -> new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal());
    }
    @Test public void medicationCombinesProductAtcAndIngredientAndDoesNotTruncateFrequency() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<String,String>();
        values.put("Medikationstyp", "Medikationsaussage"); values.put("Beginn", "2026-01-01");
        values.put("Ende", "2026-01-10"); values.put("Dokumentationszeitpunkt", "2026-01-11");
        values.put("Präparatcode", "00000000"); values.put("Präparatcodesystem", "PZN");
        values.put("ATC-Code", "N06AA09"); values.put("ATC-Version", "2025");
        values.put("Wirkstoffcode", "!dar:unknown"); values.put("Wirkstoffcodesystem", "ASK");
        values.put("Einzeldosis", "2"); values.put("Dosiereinheit", "mg"); values.put("Dosen pro Tag", "0.5");
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        Medication medication = (Medication)resources.get(0);
        assertEquals(2, medication.getCode().getCoding().size());
        assertEquals("2025", medication.getCode().getCoding().get(1).getVersion());
        assertFalse(medication.getIngredientFirstRep().hasStrength());
        MedicationStatement statement = (MedicationStatement)resources.get(1);
        assertEquals("2026-01-10", statement.getEffectivePeriod().getEndElement().getValueAsString());
        assertEquals("2026-01-11", statement.getDateAssertedElement().getValueAsString());
        assertEquals("Einzeldosis: 2 mg; Dosen pro Tag: 0.5", statement.getDosageFirstRep().getText());
        assertFalse(statement.getDosageFirstRep().hasTiming());
        values.put("ATC-Version", "2026");
        var changed = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        assertNotEquals(medication.getId(), changed.get(0).getId());
        values.put("ATC-Code", "!dar:unknown");
        var missing = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        Coding atc = ((Medication)missing.get(0)).getCode().getCoding().get(1);
        assertFalse(atc.getCodeElement().hasValue());
        assertEquals("unknown", atc.getCodeElement().getExtensionFirstRep().getValue().primitiveValue());
    }
    @Test public void laboratoryAndClinicalDocumentationKeepBooleanValues() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("LOINC", "1234-5", "Messwert", "true", "Werttyp", "Ja/Nein"));
        var laboratory = new ObservationLaboratoryConverter(row(values,
                ObservationLaboratoryConverter.ObservationLaboratory_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        assertTrue(((Observation)laboratory.get(0)).getValueBooleanType().booleanValue());
        values.put("Untersuchungscode", "1234-5"); values.put("Wert", "true");
        var resources = new ObservationVitalSignsConverter(row(values, ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()),
                null, new ConverterResult(options), null, options).convertInternal();
        assertTrue(((Observation)resources.get(0)).getValueBooleanType().booleanValue());
    }
    @Test public void administrationKeepsDoseWithoutInventingUnitOrSystem() throws Exception {
        ConverterOptions options = new ConverterOptions("");
        var values = new HashMap<>(Map.of("Medikationstyp", "Verabreichung", "Präparatcode", "123", "Präparatcodesystem", "RxNorm",
                "Wirkstoffcode", "!dar:unknown", "Wirkstoffcodesystem", DiagnosisValues.SNOMED,
                "Beginn", "2026-01-01", "Einzeldosis", "2"));
        var resources = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal();
        var dose = ((MedicationAdministration)resources.get(1)).getDosage().getDose();
        assertEquals("2", dose.getValue().toPlainString());
        assertFalse(dose.hasCodeElement());
        assertFalse(dose.hasUnitElement());
        assertFalse(dose.hasSystemElement());
        values.remove("Einzeldosis"); values.put("Dosierungstext", "Gabe ohne Mengenangabe");
        assertTrue(MedicationValues.errors(values::get).isEmpty());
    }
    @Test public void emptyClinicalInputsSerializeWithoutRepairValues() throws Exception {
        var options = new ConverterOptions("");
        var resources = new ArrayList<Resource>();
        resources.add(patientWith(Map.of()));
        for (String type : MedicationValues.STATUSES.keySet())
            resources.addAll(new MedicationConverter(row(Map.of("Medikationstyp", type), MedicationConverter.Medication_Columns.values()),
                    null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new ConditionConverter(row(Map.of(), ConditionConverter.Diagnosis_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new ProcedureConverter(row(Map.of(), ProcedureConverter.Procedure_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new DocumentReferenceConverter(row(Map.of(), DocumentReferenceConverter.DocumentReference_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new ObservationLaboratoryConverter(row(Map.of(), ObservationLaboratoryConverter.ObservationLaboratory_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new ObservationVitalSignsConverter(row(Map.of(), ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
        var event = row(Map.of("Eintrag ID", "empty"), ClinicalEventConverter.Columns.values());
        resources.addAll(new ClinicalEventConverter.Vaccine(event, null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new ClinicalEventConverter.Report(event, null, new ConverterResult(options), null, options).convertInternal());
        resources.addAll(new CarePlanConverter(row(Map.of(), CarePlanConverter.Columns.values()), null, new ConverterResult(options), null, options).convertInternal());
        for (Resource resource : resources) {
            for (var format : List.of(OutputFileType.JSON, OutputFileType.XML)) {
                String encoded = format.getParser().encodeResourceToString(resource);
                assertFalse(resource.fhirType() + ": " + encoded, encoded.contains("data-absent-reason"));
                var restored = (Resource)format.getParser().parseResource(encoded);
                var json = new com.fasterxml.jackson.databind.ObjectMapper().readTree(OutputFileType.JSON.getParser().encodeResourceToString(restored));
                for (String absent : List.of("status", "intent", "category", "gender", "name", "birthDate", "address", "docStatus", "date", "type", "content"))
                    assertFalse(resource.fhirType() + " invented " + absent + ": " + encoded, json.has(absent));
            }
        }
    }

    @Test public void partialCodingsAndExplicitAddressReasonsKeepOnlyEnteredFacts() throws Exception {
        var options = new ConverterOptions("");
        for (var values : List.of(Map.of("Code", "00123"), Map.of("Codesystem", "https://example.org/custom"),
                Map.of("Version", "custom edition"), Map.of("Code", "!dar:masked"))) {
            var condition = (Condition)new ConditionConverter(row(values, ConditionConverter.Diagnosis_Columns.values()), null,
                    new ConverterResult(options), null, options).convertInternal().get(0);
            Coding coding = condition.getCode().getCodingFirstRep();
            assertEquals(values.containsKey("Codesystem"), coding.hasSystem());
            assertEquals(values.containsKey("Code"), coding.hasCodeElement());
            assertEquals(values.containsKey("Version"), coding.hasVersion());
        }
        for (var values : List.of(Map.of("Präparatcode", "00123", "Wirkstoffcode", "ingredient"),
                Map.of("Präparatcodesystem", "RxNorm", "Wirkstoffcodesystem", "UNII"))) {
            var input = new HashMap<>(values); input.put("Medikationstyp", "Verordnung");
            assertTrue(MedicationValues.errors(input::get).toString(), MedicationValues.errors(input::get).isEmpty());
            Medication medication = (Medication)new MedicationConverter(row(input, MedicationConverter.Medication_Columns.values()),
                    null, new ConverterResult(options), null, options).convertInternal().get(0);
            assertEquals(values.containsKey("Präparatcode"), medication.getCode().getCodingFirstRep().hasCode());
            assertEquals(values.containsKey("Präparatcodesystem"), medication.getCode().getCodingFirstRep().hasSystem());
            assertEquals(values.containsKey("Wirkstoffcode"), medication.getIngredientFirstRep().getItemCodeableConcept().getCodingFirstRep().hasCode());
        }
        Patient patient = patientWith(Map.of("Straße", "!dar:masked", "Ort", "Unbekannt", "Geschlecht", "unknown"));
        assertEquals("unknown", patient.getGenderElement().getValueAsString());
        assertEquals("masked", patient.getAddressFirstRep().getLine().get(0).getExtensionFirstRep().getValue().primitiveValue());
        assertEquals("Unbekannt", patient.getAddressFirstRep().getCity());
        assertFalse(patient.getAddressFirstRep().hasType());
        assertNull(EncounterConverter.createDiagnosisUse(null));
        assertNull(EncounterConverter.createDiagnosisUse(""));
        assertFalse(Converter.createCoding("https://example.org/system", (String)null).hasCodeElement());
    }

    @Test public void procedureDocumentAndCarePlanPreserveExplicitValuesWithoutInferredExtras() throws Exception {
        var options = new ConverterOptions("");
        Procedure procedure = (Procedure)new ProcedureConverter(row(Map.of("Prozedurencode", "5-511.y", "Zusatzcode", "extra"),
                ProcedureConverter.Procedure_Columns.values()), null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals(2, procedure.getCode().getCoding().size());
        assertEquals("5-511.y", procedure.getCode().getCodingFirstRep().getCode());
        assertFalse(procedure.getCode().getCodingFirstRep().hasSystem());
        assertFalse(procedure.getCode().getCodingFirstRep().hasVersion());
        assertFalse(procedure.hasCategory());
        DocumentReference document = (DocumentReference)new DocumentReferenceConverter(row(Map.of("Dokumentbezeichner", "Freier Dokumenttyp", "Ausgabezeitpunkt", "!dar:not-asked"),
                DocumentReferenceConverter.DocumentReference_Columns.values()), null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals("Freier Dokumenttyp", document.getType().getText());
        assertFalse(document.getType().hasCoding());
        assertEquals("not-asked", document.getDateElement().getExtensionFirstRep().getValue().primitiveValue());
        CarePlan plan = (CarePlan)new CarePlanConverter(row(Map.of("Aktivitätscode", "12345"),
                CarePlanConverter.Columns.values()), null, new ConverterResult(options), null, options).convertInternal().get(0);
        assertEquals("12345", plan.getActivityFirstRep().getDetail().getCode().getCodingFirstRep().getCode());
        assertFalse(plan.getActivityFirstRep().getDetail().hasStatus());
    }
    @Test public void emptyObservationValuesStayEmptyForEverySelectedValueType() throws Exception {
        var options = new ConverterOptions("");
        for (String kind : List.of("Zahl", "Text", "Code", "Ja/Nein", "Komponenten", "Fehlend")) {
            var values = Map.of("Werttyp", kind);
            var resources = new ArrayList<Resource>();
            resources.addAll(new ObservationLaboratoryConverter(row(values, ObservationLaboratoryConverter.ObservationLaboratory_Columns.values()),
                    null, new ConverterResult(options), null, options).convertInternal());
            resources.addAll(new ObservationVitalSignsConverter(row(values, ObservationVitalSignsConverter.ObservationVitalSigns_Columns.values()),
                    null, new ConverterResult(options), null, options).convertInternal());
            for (Resource resource : resources) {
                Observation observation = (Observation)resource;
                assertFalse(kind, observation.hasValue());
                assertFalse(kind, observation.hasDataAbsentReason());
            }
        }
    }
    @Test public void medicationProductIdentityRetainsEverySupportedProductFact() throws Exception {
        var options = new ConverterOptions("");
        var base = new HashMap<String, String>();
        base.put("Medikationstyp", "Verordnung");
        base.put("Präparatbezeichnung", "Product"); base.put("Präparatcode", "123");
        base.put("Präparatcodesystem", "PZN"); base.put("ATC-Code", "N06AA09");
        base.put("ATC-Version", "2026"); base.put("Darreichungsform", "Tablet");
        base.put("Wirkstoffcode", "ingredient"); base.put("Wirkstoffcodesystem", "ASK");
        var medication = new MedicationConverter(row(base, MedicationConverter.Medication_Columns.values()), null,
                new ConverterResult(options), null, options).convertInternal().get(0);
        for (var change : Map.of("Präparatbezeichnung", "Another product", "Präparatcode", "456",
                "Präparatcodesystem", "RxNorm", "ATC-Code", "N06AA10", "ATC-Version", "2025",
                "Darreichungsform", "Solution", "Wirkstoffcode", "other", "Wirkstoffcodesystem", "UNII").entrySet()) {
            var values = new HashMap<>(base); values.put(change.getKey(), change.getValue());
            var changed = new MedicationConverter(row(values, MedicationConverter.Medication_Columns.values()), null,
                    new ConverterResult(options), null, options).convertInternal().get(0);
            assertNotEquals(change.getKey(), medication.getId(), changed.getId());
        }
    }

}
