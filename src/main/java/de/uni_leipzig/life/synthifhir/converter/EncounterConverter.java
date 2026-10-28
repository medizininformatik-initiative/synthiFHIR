package de.uni_leipzig.life.synthifhir.converter;

import static com.google.common.base.Strings.isNullOrEmpty;
import static de.uni_leipzig.life.synthifhir.ConverterOptions.IntOption.START_ID_ENCOUNTER_LEVEL_2;
import static de.uni_leipzig.life.synthifhir.ConverterOptions.IntOption.START_ID_ENCOUNTER_LEVEL_3;
import static de.uni_leipzig.life.synthifhir.TableIdentifier.Fall;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Bett;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Einrichtungskontaktklasse;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Ende;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Fachabteilung;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Start;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Station;
import static de.uni_leipzig.life.synthifhir.converter.EncounterConverter.Encounter_Columns.Zimmer;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import org.apache.commons.csv.CSVRecord;
import org.hl7.fhir.r4.model.CodeableConcept;
import org.hl7.fhir.r4.model.Coding;
import org.hl7.fhir.r4.model.Condition;
import org.hl7.fhir.r4.model.Encounter;
import org.hl7.fhir.r4.model.Encounter.DiagnosisComponent;
import org.hl7.fhir.r4.model.Encounter.EncounterStatus;
import org.hl7.fhir.r4.model.Location;
import org.hl7.fhir.r4.model.Organization;
import org.hl7.fhir.r4.model.Location.LocationStatus;
import org.hl7.fhir.r4.model.Meta;
import org.hl7.fhir.r4.model.Period;
import org.hl7.fhir.r4.model.Procedure;
import org.hl7.fhir.r4.model.Reference;
import org.hl7.fhir.r4.model.Resource;

import com.google.common.base.Strings;
import com.google.common.collect.ImmutableList;

import de.uni_leipzig.imise.validate.FHIRValidator;
import de.uni_leipzig.life.synthifhir.CodeSystemMapper;
import de.uni_leipzig.life.synthifhir.ContactIndex.Level;
import de.uni_leipzig.life.synthifhir.Converter;
import de.uni_leipzig.life.synthifhir.ConverterOptions;
import de.uni_leipzig.life.synthifhir.ConverterResult;
import de.uni_leipzig.life.synthifhir.SupportResourceIdentity;
import de.uni_leipzig.life.synthifhir.TableColumnIdentifier;

/**
 * @author jheuschkel (19.10.2020), AXS (05.11.2021)
 */
public class EncounterConverter extends Converter {
    private final CSVRecord inputRecord;

    public enum ContactColumn implements TableColumnIdentifier {
        Kontaktart;
        @Override public boolean isMandatory() { return false; }
    }

    public static final Map<String, String> CONTACT_KINDS = Map.of(
            "Normalstationär", "normalstationaer", "Intensivstationär", "intensivstationaer",
            "Operation", "operation", "Untersuchung und Behandlung", "ub", "Konsil", "konsil");
    private static final java.util.Set<String> SECONDARY_KINDS = java.util.Set.of(
            "Operation", "Untersuchung und Behandlung", "Konsil");
    private final ContactConversionState state;

    private String value(Object column) {
        String text = get(column);
        return text == null || text.isBlank() ? null : text.trim();
    }

    private void period(Encounter encounter, Period period) {
        encounter.setPeriod(period);
        encounter.setStatus(period.hasEnd() ? EncounterStatus.FINISHED : EncounterStatus.INPROGRESS);
        for (var location : encounter.getLocation()) {
            location.setPeriod(period.copy());
            location.setStatus(period.hasEnd() ? Encounter.EncounterLocationStatus.COMPLETED
                    : Encounter.EncounterLocationStatus.ACTIVE);
        }
    }

    private void derivedEnd(Encounter child, Encounter source) {
        Period p = child.getPeriod();
        p.setEndElement(source.getPeriod().getEndElement().copy());
        period(child, p);
        Map<String, String> note = state.derivedEnds.get(child);
        if (note == null) {
            note = new java.util.LinkedHashMap<>();
            note.put("encounter", child.getId());
            note.put("record", Long.toString(inputRecord.getRecordNumber()));
            note.put("reason", "Missing encounter end derived from the associated primary or facility encounter, independently of procedure times.");
            result.contactEndDerivations.add(note);
            state.derivedEnds.put(child, note);
        }
        note.put("sourceEncounter", source.getId());
        note.put("end", p.hasEnd() ? p.getEndElement().getValueAsString() : "offen");
    }

    private void closePrimary(org.hl7.fhir.r4.model.DateTimeType boundary) {
        if (state.primaryContact == null) return;
        if (!state.primaryContact.getPeriod().hasEnd() || state.primaryEndDerived) {
            state.primaryContact.getPeriod().setEndElement(boundary.copy());
            period(state.primaryContact, state.primaryContact.getPeriod());
            if (state.derivedEnds.containsKey(state.primaryContact)) {
                state.derivedEnds.get(state.primaryContact).put("end", boundary.getValueAsString());
                state.derivedEnds.get(state.primaryContact).put("reason", "Missing primary encounter end derived from the start of the next primary stay.");
            }
            for (Encounter secondary : state.secondaryContacts)
                if (state.derivedEnds.containsKey(secondary)) derivedEnd(secondary, state.primaryContact);
        }
        state.secondaryContacts.clear();
    }

    private Encounter newContact(Encounter encounter, String id, Encounter parent, Period p, String kind) throws Exception {
        encounter.setId(id);
        encounter.setSubject(getPatientReference());
        encounter.setMeta(getMeta());
        encounter.setType(new ArrayList<>(getEncounterType(encounter.getClass())));
        if (parent != null) {
            encounter.setPartOf(new Reference("Encounter/" + parent.getId()));
            encounter.setClass_(state.previousEncounterLevel1.getClass_().copy());
        } else encounter.setClass_(getEncounterLevel1Class());
        if (kind != null) encounter.addType(createCodeableConcept("http://fhir.de/CodeSystem/kontaktart-de", CONTACT_KINDS.get(kind), kind, null));
        period(encounter, p.copy());
        return encounter;
    }

    private void locations(Encounter encounter, String department, List<Resource> resources) throws Exception {
        Location parent = null;
        String path = SupportResourceIdentity.key(department);
        String station = value(Station);
        var configuration = options.configuration();
        String providerMode = configuration == null ? "none" : configuration.effective("resource.Encounter.stationServiceProvider")
                .map(v -> v.asText()).orElse("none");
        boolean stationLocation = configuration == null || configuration.effective("resource.Encounter.stationLocation")
                .map(v -> v.asBoolean()).orElse(true);
        if (station != null && !providerMode.equals("none")) {
            Organization organization = new Organization();
            organization.setId(SupportResourceIdentity.place(options, "Organization", path + SupportResourceIdentity.key("wa", station), encounter.getId()));
            organization.setName(station).setActive(true);
            String reference = "Organization/" + organization.getIdElement().getIdPart();
            if (providerMode.equals("contained")) {
                encounter.addContained(organization);
                reference = "#" + organization.getIdElement().getIdPart();
            } else if (providerMode.equals("generate-reference")) resources.add(organization);
            encounter.setServiceProvider(new Reference(reference).setDisplay(station));
        }
        var types = Map.of(Station, "wa", Zimmer, "ro", Bett, "bd");
        for (var field : List.of(Station, Zimmer, Bett)) {
            String name = value(field);
            if (name == null) continue;
            String type = types.get(field);
            path += SupportResourceIdentity.key(type, name);
            if (field == Station && !stationLocation) continue;
            Location location = new Location();
            location.setId(SupportResourceIdentity.place(options, "Location",
                    SupportResourceIdentity.key(path, parent == null ? "" : "Location/" + parent.getId()), encounter.getId()));
            location.setName(name).setStatus(LocationStatus.ACTIVE);
            location.setPhysicalType(new CodeableConcept(new Coding("http://terminology.hl7.org/CodeSystem/location-physical-type", type, null)));
            if (parent != null) location.setPartOf(new Reference("Location/" + parent.getId()));
            encounter.addLocation().setLocation(new Reference("Location/" + location.getId()))
                    .setPhysicalType(location.getPhysicalType().copy());
            resources.add(location);
            parent = location;
        }
        period(encounter, encounter.getPeriod());
    }

    @Override protected List<Resource> convertInternal() throws Exception {
        var inputEnd = ClinicalValues.date(value(Ende));
        boolean inputEndMissing = inputEnd == null || !inputEnd.hasValue();
        List<Resource> resources = convertContacts();
        for (Resource resource : resources) {
            if (!(resource instanceof Encounter)) continue;
            Encounter encounter = (Encounter)resource;
            var level = encounter instanceof EncounterLevel1 ? Level.FACILITY
                    : encounter instanceof EncounterLevel2 ? Level.DEPARTMENT
                    : Level.WARD_SERVICE;
            String kind = level == Level.WARD_SERVICE ? value(ContactColumn.Kontaktart) : null;
            result.contacts().add(encounter, getPatientId(), state.previousEncounterLevel1.getId(), level,
                    encounter.hasPartOf() ? encounter.getPartOf().getReferenceElement().getIdPart() : null,
                    inputRecord.getRecordNumber(), SECONDARY_KINDS.contains(kind == null ? "" : kind), inputEndMissing);
        }
        return resources;
    }

    private List<Resource> convertContacts() throws Exception {
        // Retired explicit hierarchy input must never be silently reinterpreted.
        for (String old : List.of("Kontakt-ID", "Kontaktebene", "Übergeordneter Kontakt")) {
            if (inputRecord.isMapped(old) && !isNullOrEmpty(inputRecord.get(old)))
                throw new IllegalArgumentException("Unsupported encounter column " + old + ": use the implicit encounter structure");
        }
        String kind = value(ContactColumn.Kontaktart);
        if (kind != null && !CONTACT_KINDS.containsKey(kind)) throw new IllegalArgumentException("Unknown encounter type: " + kind);
        boolean secondary = SECONDARY_KINDS.contains(kind == null ? "" : kind);
        String department = value(Fachabteilung);
        boolean hasPlaces = value(Station) != null || value(Zimmer) != null || value(Bett) != null;
        if (kind != null && !hasPlaces) throw new IllegalArgumentException("Kontaktart requires at least Station, Zimmer or Bett");
        Period p = new Period().setStartElement(ClinicalValues.date(value(Start))).setEndElement(ClinicalValues.date(value(Ende)));
        if (!p.hasStart() || !p.getStartElement().hasValue()) throw new IllegalArgumentException("Encounter start is required");
        String rootId = getEncounterId();
        boolean newRoot = state.previousEncounterLevel1 == null || !state.previousEncounterLevel1.getSubject().getReference().equals(getPatientReference().getReference())
                || (!isNullOrEmpty(rootId) && !rootId.equals(state.previousEncounterLevel1.getId()));
        if (newRoot && (secondary || isNullOrEmpty(rootId))) throw new IllegalArgumentException("The facility encounter must precede its stays");
        if (!newRoot) {
            if (value(Einrichtungskontaktklasse) != null
                    && !Objects.equals(getEncounterLevel1Class().getCode(), state.previousEncounterLevel1.getClass_().getCode()))
                throw new IllegalArgumentException("Conflicting facility encounter classes within the same case");
            if (value(Encounter_Columns.Aufnahmegrund) != null) throw new IllegalArgumentException("Specify Aufnahmegrund in the first case row only");
        }
        if (secondary) {
            if (state.primaryContact == null) throw new IllegalArgumentException("A secondary encounter requires a preceding primary location encounter");
            Encounter parent = state.previousEncounterLevel2 != null ? state.previousEncounterLevel2 : state.previousEncounterLevel1;
            String id = state.previousEncounterLevel1.getId() + ResourceIdSuffix.ENCOUNTER_LEVEL_3
                    + result.getNextId(Fall, EncounterLevel3.class, START_ID_ENCOUNTER_LEVEL_3);
            Encounter contact = newContact(new EncounterLevel3(), id, parent, p, kind);
            if (department != null) contact.setServiceType(createCodeableConcept(Fachabteilung, ENCOUNTER_LEVEL2_DEPARTMENT_RESOURCES));
            if (!p.hasEnd()) derivedEnd(contact, state.primaryContact);
            state.secondaryContacts.add(contact);
            List<Resource> resources = new ArrayList<>(); resources.add(contact);
            locations(contact, department, resources);
            return resources;
        }
        if (!newRoot && state.primaryContact != null && (hasPlaces || department != null)) {
            closePrimary(p.getStartElement());
        }
        List<Resource> resources = new ArrayList<>();
        if (newRoot) {
            state.previousEncounterLevel1 = newContact(new EncounterLevel1(), rootId, null, p, null);
            var reason = AdmissionReasonValues.extension(value(Encounter_Columns.Aufnahmegrund));
            if (reason != null) state.previousEncounterLevel1.addExtension(reason);
            resources.add(state.previousEncounterLevel1);
            state.previousEncounterLevel2 = null; state.previousDepartmentName = null;
            state.primaryContact = null; state.secondaryContacts.clear();
            state.facilityBound = department == null && !hasPlaces;
        } else if (!state.facilityBound) {
            // Original CSV continuation convention: the first row can also contain
            // the first ward stay; later primary rows extend the facility period.
            updateParentPeriodAndStatus(state.previousEncounterLevel1, p);
        }
        if (department != null && !department.equals(state.previousDepartmentName)) {
            if (state.previousEncounterLevel2 != null && !state.departmentBound) {
                state.previousEncounterLevel2.getPeriod().setEndElement(p.getStartElement().copy());
                period(state.previousEncounterLevel2, state.previousEncounterLevel2.getPeriod());
            }
            String id = state.previousEncounterLevel1.getId() + ResourceIdSuffix.ENCOUNTER_LEVEL_2
                    + result.getNextId(Fall, EncounterLevel2.class, START_ID_ENCOUNTER_LEVEL_2);
            state.previousEncounterLevel2 = newContact(new EncounterLevel2(), id, state.previousEncounterLevel1, p, null);
            state.previousEncounterLevel2.setServiceType(createCodeableConcept(Fachabteilung, ENCOUNTER_LEVEL2_DEPARTMENT_RESOURCES));
            state.previousDepartmentName = department;
            state.departmentBound = !hasPlaces && p.hasEnd();
            resources.add(state.previousEncounterLevel2);
        } else if (state.previousEncounterLevel2 != null && hasPlaces && !state.departmentBound) {
            updateParentPeriodAndStatus(state.previousEncounterLevel2, p);
        }
        if (hasPlaces) {
            String id = state.previousEncounterLevel1.getId() + ResourceIdSuffix.ENCOUNTER_LEVEL_3
                    + result.getNextId(Fall, EncounterLevel3.class, START_ID_ENCOUNTER_LEVEL_3);
            state.primaryContact = newContact(new EncounterLevel3(), id,
                    state.previousEncounterLevel2 != null ? state.previousEncounterLevel2 : state.previousEncounterLevel1, p, kind);
            state.primaryEndDerived = !p.hasEnd();
            if (state.primaryEndDerived) derivedEnd(state.primaryContact, state.previousEncounterLevel1);
            if (!state.departmentBound) updateParentPeriodAndStatus(state.previousEncounterLevel2, state.primaryContact.getPeriod());
            resources.add(state.primaryContact);
            locations(state.primaryContact, department, resources);
        } else if (department != null) state.primaryContact = null;
        return resources;
    }

    /**
     * toString() result of these enum values are the names of the columns in the
     * correspunding excel sheet.
     */
    public static enum Encounter_Columns implements TableColumnIdentifier {
        Start,
        Ende,
        Einrichtungskontaktklasse,
        Fachabteilung,
        Station,
        Zimmer,
        Bett,
        Aufnahmegrund;

        @Override
        public String toString() {
            return this == Aufnahmegrund ? AdmissionReasonValues.COLUMN : name();
        }
    }

    /** Map with the encounter types and its displays. */
    public static final CodeSystemMapper ENCOUNTER_TYPE_RESOURCES = new CodeSystemMapper("Encounter_Type.map");

    /**
     * Maps from human readable encounter types to the correspondig code system
     * code and contains some more resources for the encounters.
     */
    public static final CodeSystemMapper ENCOUNTER_LEVEL1_CLASS_RESOURCES = new CodeSystemMapper(
            "EncounterLevel1_Class.map");

    /**
     * Maps from human readable department description to the number code for the
     * department.
     */
    private final CodeSystemMapper ENCOUNTER_LEVEL2_DEPARTMENT_RESOURCES = new CodeSystemMapper(
            "EncounterLevel2_Department.map");

    /**
     * Maps from human readable diagnosis role description to the correspondig code
     * system code.
     */
    public static final CodeSystemMapper DIAGNOSIS_ROLE_RESOURCES = new CodeSystemMapper("Diagnosis_Role.map");

    /**
     * Even if they (unfortunately) do not exist in the KDS, they are created here
     * because of actual encounter types. This allows us to distinguish them
     * clearly.
     */
    public static class EncounterLevel1 extends Encounter {
    }

    public static class EncounterLevel2 extends Encounter {
    }

    public static class EncounterLevel3 extends Encounter {
    }

    /**
     * @param record
     * @param previousRecordPID
     * @param result
     * @param validator
     * @param options
     * @throws Exception
     */
    public EncounterConverter(CSVRecord record, String previousRecordPID, ConverterResult result,
            FHIRValidator validator, ConverterOptions options) throws Exception {
        super(record, previousRecordPID, result, validator, options);
        inputRecord = record;
        state = result.contactState();
    }

    private static void updateParentPeriodAndStatus(Encounter parentEncounter, Period period) {
        if (parentEncounter == null) {
            return;
        }
        Period parentPeriod = parentEncounter.getPeriod();
        if (!period.hasEnd() || !parentPeriod.hasEnd() || period.getEnd().after(parentPeriod.getEnd())) {
            parentPeriod.setEndElement(period.getEndElement().copy());
        }
        parentEncounter.setStatus(parentPeriod.hasEnd() ? EncounterStatus.FINISHED : EncounterStatus.INPROGRESS);
    }

    /**
     * @return
     * @throws Exception
     */
    private Coding getEncounterLevel1Class() throws Exception {
        Coding coding = createCoding(ENCOUNTER_LEVEL1_CLASS_RESOURCES.getCodeSystem(), Einrichtungskontaktklasse);
        return setCorrectCodeAndDisplayInClassCoding(coding);
    }

    /**
     * @param coding
     */
    private static Coding setCorrectCodeAndDisplayInClassCoding(Coding coding) {
        if (coding != null) {
            // replace the code string from by the correct code and display from the
            // resource map
            String code = coding.getCode();
            if (!Strings.isNullOrEmpty(code)) { // is null if the Coding has only a Data Absent Reason Extension
                String realCode = ENCOUNTER_LEVEL1_CLASS_RESOURCES.get(code);
                String display = ENCOUNTER_LEVEL1_CLASS_RESOURCES.getFirstBackwardKey(realCode);
                coding.setCode(realCode);
                coding.setDisplay(display);
            }
        }
        return coding;
    }

    /**
     * @return
     */
    protected static Meta getMeta() {
        return new Meta().addProfile(ENCOUNTER_LEVEL1_CLASS_RESOURCES.getProfile());
    }

    /**
     * @param useIdentifier
     * @return
     */
    public static CodeableConcept createDiagnosisUse(String useIdentifier) {
        if (useIdentifier == null || useIdentifier.isBlank()) return null;
        String codeSystem = DIAGNOSIS_ROLE_RESOURCES.getCodeSystem();
        String code = DIAGNOSIS_ROLE_RESOURCES.getHumanToCode(useIdentifier);
        String display = DIAGNOSIS_ROLE_RESOURCES.getCodeToHuman(code);
        CodeableConcept diagnosisUse = new CodeableConcept();
        diagnosisUse.addCoding()
                .setSystem(codeSystem)
                .setCode(code)
                .setDisplay(display);
        return diagnosisUse;
    }

    /**
     * @param result
     * @param encounterID
     * @param procedure
     */
    public static void addDiagnosisToEncounter(ConverterResult result, String encounterID, Procedure procedure) {
        addDiagnosisResourceToEncounter(result, encounterID, procedure, null);
    }

    /**
     * @param result
     * @param encounterID
     * @param condition
     * @param diagnosisUseIdentifier
     */
    public static void addDiagnosisToEncounter(ConverterResult result, String encounterID, Condition condition,
            String diagnosisUseIdentifier) {
        addDiagnosisResourceToEncounter(result, encounterID, condition, diagnosisUseIdentifier);
    }

    /**
     * @param result
     * @param encounterID
     * @param conditionOrProcedureAsDiagnosis
     * @param diagnosisUseIdentifier
     */
    private static void addDiagnosisResourceToEncounter(ConverterResult result, String encounterID,
            Resource conditionOrProcedureAsDiagnosis, String diagnosisUseIdentifier) {
        // encounter should be only null in error cases, but mybe we
        // should catch and log
        Encounter encounter = result.get(Fall, Encounter.class, encounterID);
        addDiagnosisResourceToEncounter(encounter, conditionOrProcedureAsDiagnosis, diagnosisUseIdentifier);
    }

    /**
     * @param encounter
     * @param conditionOrProcedureAsDiagnosis
     * @param diagnosisUseIdentifier
     */
    private static void addDiagnosisResourceToEncounter(Encounter encounter, Resource conditionOrProcedureAsDiagnosis,
            String diagnosisUseIdentifier) {
        // the encounter can be null, if the diagnosis is defined with an
        // empty or not present encounter number in the current dataset
        if (encounter == null) {
            return;
        }
        // construct a valid DiagnosisComponent from condition or
        // procedure to add it as reference to the encounter
        // Output policies resolve the target before serialization materializes object references.
        Reference conditionReference = new Reference(conditionOrProcedureAsDiagnosis)
                .setReference(conditionOrProcedureAsDiagnosis.fhirType() + "/"
                        + conditionOrProcedureAsDiagnosis.getIdElement().getIdPart());
        // add diagnosis use to the diagnosis component
        CodeableConcept diagnosisUse = EncounterConverter.createDiagnosisUse(diagnosisUseIdentifier);
        DiagnosisComponent diagnosisComponent = new DiagnosisComponent(conditionReference);
        diagnosisComponent.setUse(diagnosisUse);
        // maybe the same diagnosis was coded twice
        if (!encounter.getDiagnosis().contains(diagnosisComponent)) {
            encounter.addDiagnosis(diagnosisComponent);
        }
    }

    /**
     * @param encounterClass
     * @return the type of the encounter as {@link CodeableConcept}.
     */
    protected List<CodeableConcept> getEncounterType(Class<? extends Encounter> encounterClass) {
        String simpleClassName = encounterClass.getSimpleName();
        String codeSystemURL = ENCOUNTER_TYPE_RESOURCES.getCodeSystem();
        String typeCode = ENCOUNTER_TYPE_RESOURCES.get(simpleClassName + "_TYPE_CODE");
        String typeDisplay = ENCOUNTER_TYPE_RESOURCES.get(simpleClassName + "_TYPE_DISPLAY");
        return ImmutableList.of(createCodeableConcept(codeSystemURL, typeCode, typeDisplay, null));
    }

    // /**
    // * @return
    // * @throws Exception
    // */
    // private List<EncounterLocationComponent> convertLocation() throws Exception
    // {
    // Identifier identifier = new Identifier();
    // identifier.setSystem("https://diz.mii.de/fhir/CodeSystem/TestOrganisationAbteilungen");
    // identifier.setValue(get(Fachabteilung));
    // Reference reference = new Reference();
    // reference.setIdentifier(identifier);
    //
    // EncounterLocationComponent encounterLocationComponent = new
    // EncounterLocationComponent();
    // encounterLocationComponent.setLocation(reference);
    // encounterLocationComponent.setStatus(Encounter.EncounterLocationStatus.COMPLETED);
    // encounterLocationComponent.setPeriod(createPeriod(Start, Ende));
    // return Collections.singletonList(encounterLocationComponent);
    // }
    //
    /**
     * @param encounter
     * @return
     */
    public static final boolean isLevel1Encounter(Encounter encounter) {
        return encounter instanceof EncounterLevel1 || encounter.getType().stream()
                .flatMap(type -> type.getCoding().stream())
                .anyMatch(coding -> "http://fhir.de/CodeSystem/Kontaktebene".equals(coding.getSystem())
                        && "einrichtungskontakt".equals(coding.getCode()));
    }

}
