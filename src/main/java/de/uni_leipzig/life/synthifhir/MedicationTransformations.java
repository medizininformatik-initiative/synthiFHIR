package de.uni_leipzig.life.synthifhir;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.HashSet;
import java.util.UUID;

import org.hl7.fhir.r4.model.*;

/** Request replacement, followed by one non-recursive administration/statement pass. */
public final class MedicationTransformations {
    private static final String PROFILE = "https://www.medizininformatik-initiative.de/fhir/core/modul-medikation/StructureDefinition/";
    private final ConverterResult input;
    private final ContractConfiguration configuration;
    private final List<Map<String, String>> changes = new ArrayList<>();
    private final Set<String> replaced = new HashSet<>();
    private final Set<String> identities = new HashSet<>();

    public MedicationTransformations(ConverterResult input) {
        this.input = input;
        configuration = input.getConverterOptions().configuration();
    }
    static boolean supports(String id) {
        return Set.of("medication.requestTreatment", "medication.MedicationAdministration.treatment",
                "medication.MedicationStatement.treatment").contains(id);
    }
    public List<Resource> apply(List<Resource> resources) {
        if (configuration == null) return resources;
        for (Resource resource : resources) identities.add(resource.fhirType() + "/" + resource.getIdElement().getIdPart());
        List<Resource> intermediate = new ArrayList<>();
        for (Resource resource : resources) {
            String action = resource instanceof MedicationRequest ? action("medication.requestTreatment") : "retain";
            if (action.startsWith("replace-")) intermediate.add(derive(resource, action, true));
            else intermediate.add(resource);
        }
        List<Resource> output = new ArrayList<>();
        for (Resource resource : intermediate) {
            String action = resource instanceof MedicationAdministration ? action("medication.MedicationAdministration.treatment")
                    : resource instanceof MedicationStatement ? action("medication.MedicationStatement.treatment") : "retain";
            if (!action.startsWith("replace-")) output.add(resource);
            if (!action.equals("retain")) output.add(derive(resource, action, false));
        }
        return output;
    }
    /** Stable counter allocation includes stored actions even when output dependencies disable them. */
    List<Resource> potentialResources(List<Resource> sources) {
        if (configuration == null) return sources;
        List<Resource> potential = new ArrayList<>(sources);
        List<Resource> intermediate = new ArrayList<>();
        for (Resource source : sources) {
            String action = source instanceof MedicationRequest ? configuration.stored("medication.requestTreatment").asText() : "retain";
            if (action.startsWith("replace-")) {
                Resource target = potentialTarget(source, action, true);
                potential.add(target); intermediate.add(target);
            } else intermediate.add(source);
        }
        for (Resource source : intermediate) {
            String action = source instanceof MedicationAdministration ? configuration.stored("medication.MedicationAdministration.treatment").asText()
                    : source instanceof MedicationStatement ? configuration.stored("medication.MedicationStatement.treatment").asText() : "retain";
            if (!action.equals("retain")) potential.add(potentialTarget(source, action, false));
        }
        return potential;
    }
    private Resource potentialTarget(Resource source, String action, boolean requestPass) {
        String type = action.endsWith("administration") ? "MedicationAdministration" : "MedicationStatement";
        Resource target = type.equals("MedicationAdministration") ? new MedicationAdministration() : new MedicationStatement();
        target.setId(derivedId(source, type, requestPass));
        Type medication = source instanceof MedicationRequest ? ((MedicationRequest)source).getMedication()
                : source instanceof MedicationAdministration ? ((MedicationAdministration)source).getMedication()
                : ((MedicationStatement)source).getMedication();
        if (medication != null) {
            if (target instanceof MedicationAdministration) ((MedicationAdministration)target).setMedication(medication.copy());
            else ((MedicationStatement)target).setMedication(medication.copy());
        }
        if (input.inputContext(source) != null) input.recordInput(target, input.inputContext(source));
        return target;
    }
    private static String derivedId(Resource source, String type, boolean requestPass) {
        String identity = source.fhirType() + "/" + source.getIdElement().getIdPart() + "/" + type + "/" + (requestPass ? "request" : "event");
        return "derived-" + UUID.nameUUIDFromBytes(identity.getBytes(StandardCharsets.UTF_8));
    }
    private String action(String id) {
        return configuration.effective(id).map(value -> value.asText()).orElse("retain");
    }
    private Resource derive(Resource source, String action, boolean requestPass) {
        if (((DomainResource)source).hasModifierExtension())
            throw new IllegalArgumentException("Cannot transform medication modifier extensions without a semantic mapping: " + source.getId());
        String type = action.endsWith("administration") ? "MedicationAdministration" : "MedicationStatement";
        Resource output = type.equals("MedicationAdministration") ? new MedicationAdministration() : new MedicationStatement();
        output.setId(derivedId(source, type, requestPass));
        if (!identities.add(output.fhirType() + "/" + output.getIdElement().getIdPart()))
            throw new IllegalStateException("Derived medication resource ID collision: " + output.getId());
        ((DomainResource)output).setMeta(new Meta().addProfile(PROFILE + type));
        if (source.hasMeta()) {
            source.getMeta().getSecurity().forEach(c -> output.getMeta().addSecurity(c.copy()));
            source.getMeta().getTag().forEach(c -> output.getMeta().addTag(c.copy()));
        }
        List<String> omitted = new ArrayList<>();
        Type medication;
        Reference subject, encounter;
        List<Annotation> notes;
        List<CodeableConcept> reasons;
        List<Reference> reasonReferences;
        if (source instanceof MedicationRequest) {
            var request = (MedicationRequest)source;
            medication = request.getMedication(); subject = request.getSubject(); encounter = request.getEncounter(); notes = request.getNote();
            reasons = request.getReasonCode(); reasonReferences = request.getReasonReference();
            // Prescription status/time and intended administration are not evidence of a performed event.
            if (request.hasStatus()) omitted.add("status");
            if (request.hasAuthoredOn()) omitted.add("authoredOn");
            if (output instanceof MedicationStatement) {
                var statement = (MedicationStatement)output;
                for (Dosage dosage : request.getDosageInstruction()) statement.addDosage(dosage.copy());
            } else if (request.hasDosageInstruction()) omitted.add("dosageInstruction");
        } else if (source instanceof MedicationAdministration) {
            var administration = (MedicationAdministration)source;
            medication = administration.getMedication(); subject = administration.getSubject(); encounter = administration.getContext(); notes = administration.getNote();
            reasons = administration.getReasonCode(); reasonReferences = administration.getReasonReference();
            var statement = (MedicationStatement)output;
            if (administration.hasEffective()) statement.setEffective(administration.getEffective().copy());
            String status = administration.hasStatus() ? administration.getStatusElement().getValueAsString() : null;
            if (status != null && Set.of("completed", "entered-in-error", "stopped", "on-hold", "unknown").contains(status))
                statement.setStatus(MedicationStatement.MedicationStatementStatus.fromCode(status));
            else if (status != null) omitted.add("status");
            if (administration.hasDosage()) {
                var dose = administration.getDosage();
                Dosage dosage = new Dosage();
                if (dose.hasText()) dosage.setText(dose.getText());
                if (dose.hasSite()) dosage.setSite(dose.getSite().copy());
                if (dose.hasRoute()) dosage.setRoute(dose.getRoute().copy());
                if (dose.hasMethod()) dosage.setMethod(dose.getMethod().copy());
                if (dose.hasDose()) dosage.getDoseAndRateFirstRep().setDose(dose.getDose().copy());
                if (dose.hasRate()) dosage.getDoseAndRateFirstRep().setRate(dose.getRate().copy());
                statement.addDosage(dosage);
            }
        } else {
            var statement = (MedicationStatement)source;
            medication = statement.getMedication(); subject = statement.getSubject(); encounter = statement.getContext(); notes = statement.getNote();
            reasons = statement.getReasonCode(); reasonReferences = statement.getReasonReference();
            var administration = (MedicationAdministration)output;
            if (statement.hasEffective()) administration.setEffective(statement.getEffective().copy());
            String status = statement.hasStatus() ? statement.getStatusElement().getValueAsString() : null;
            if (status != null && Set.of("completed", "entered-in-error", "stopped", "on-hold", "unknown").contains(status))
                administration.setStatus(MedicationAdministration.MedicationAdministrationStatus.fromCode(status));
            else if (status != null) omitted.add("status");
            // A statement's regimen is not a record of an individual administered dose.
            if (statement.hasDosage()) omitted.add("dosage");
            if (statement.hasDateAsserted()) omitted.add("dateAsserted");
        }
        if (output instanceof MedicationAdministration) {
            var administration = (MedicationAdministration)output;
            if (medication != null) administration.setMedication(medication.copy());
            if (!subject.isEmpty()) administration.setSubject(subject.copy());
            if (!encounter.isEmpty()) administration.setContext(encounter.copy());
            notes.forEach(note -> administration.addNote(note.copy()));
            reasons.forEach(reason -> administration.addReasonCode(reason.copy()));
            reasonReferences.forEach(reason -> administration.addReasonReference(reason.copy()));
        } else {
            var statement = (MedicationStatement)output;
            if (medication != null) statement.setMedication(medication.copy());
            if (!subject.isEmpty()) statement.setSubject(subject.copy());
            if (!encounter.isEmpty()) statement.setContext(encounter.copy());
            notes.forEach(note -> statement.addNote(note.copy()));
            reasons.forEach(reason -> statement.addReasonCode(reason.copy()));
            reasonReferences.forEach(reason -> statement.addReasonReference(reason.copy()));
        }
        Set<String> handled = Set.of("id", "meta", "medication[x]", "subject", "context", "encounter", "note",
                "reasonCode", "reasonReference", "status", "effective[x]", "dosage", "dosageInstruction", "authoredOn", "dateAsserted");
        for (Property property : source.children()) {
            if (!handled.contains(property.getName()) && property.getValues().stream().anyMatch(value -> !value.isEmpty()))
                omitted.add(property.getName());
        }
        var context = input.inputContext(source);
        if (context != null) input.recordInput(output, context);
        boolean replacement = action.startsWith("replace-");
        if (replacement) replaced.add(source.fhirType() + "/" + source.getIdElement().getIdPart());
        Map<String, String> change = new LinkedHashMap<>();
        change.put("source", source.fhirType() + "/" + source.getIdElement().getIdPart());
        change.put("target", output.fhirType() + "/" + output.getIdElement().getIdPart());
        change.put("action", replacement ? "replace" : "add");
        if (!omitted.isEmpty()) change.put("notTransferred", String.join(",", omitted));
        List<String> missing = new ArrayList<>();
        for (String property : List.of("status", "medication", "subject", "effective")) {
            if (output.children().stream().filter(p -> p.getName().equals(property) || p.getName().equals(property + "[x]"))
                    .flatMap(p -> p.getValues().stream()).noneMatch(value -> !value.isEmpty())) missing.add(property);
        }
        if (!missing.isEmpty()) change.put("missingTargetFacts", String.join(",", missing));
        changes.add(change);
        return output;
    }
    public List<Map<String, String>> changes() { return List.copyOf(changes); }
    public Set<String> replaced() { return Set.copyOf(replaced); }
}
