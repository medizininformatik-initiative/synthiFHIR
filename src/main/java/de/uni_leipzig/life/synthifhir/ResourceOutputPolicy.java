package de.uni_leipzig.life.synthifhir;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.hl7.fhir.r4.model.*;

/** Resource selection and descriptive references, applied after internal conversion. */
public final class ResourceOutputPolicy {
    private static final Set<String> BOOLEAN_TYPES = Set.of("Condition", "Procedure", "Observation.laboratory",
            "Observation.vitalSigns", "Observation.other", "MedicationRequest", "MedicationAdministration", "MedicationStatement",
            "Immunization", "DiagnosticReport", "CarePlan", "DocumentReference", "Consent");
    private final ContractConfiguration configuration;
    private final Map<String, Resource> potential = new LinkedHashMap<>();
    private final Set<String> replaced;
    private final Set<String> omittedTargets = new java.util.HashSet<>();

    public ResourceOutputPolicy(ContractConfiguration configuration, List<Resource> resources) {
        this(configuration, resources, Set.of());
    }
    public ResourceOutputPolicy(ContractConfiguration configuration, List<Resource> resources, Set<String> replaced) {
        this.configuration = configuration;
        this.replaced = Set.copyOf(replaced);
        for (Resource resource : resources) potential.put(resource.fhirType() + "/" + resource.getIdElement().getIdPart(), resource);
        // Selection is fixed for this completed patient graph. Resolve it once,
        // avoiding recursive HAPI property lists when no target is omitted.
        potential.forEach((key, target) -> {
            if (!emits(target) && !referenceOnly(target)) omittedTargets.add(key);
        });
    }

    static boolean supports(String id) {
        return BOOLEAN_TYPES.stream().anyMatch(type -> id.equals("resource." + type + ".enabled"))
                || Set.of("resource.Location.mode", "resource.Location.existingReference", "resource.Location.description",
                        "resource.Medication.mode", "resource.Medication.existingReference", "resource.Medication.description").contains(id);
    }

    public boolean emits(Resource resource) {
        if (configuration == null) return true;
        if (replaced.contains(resource.fhirType() + "/" + resource.getIdElement().getIdPart())) return false;
        String type = resource instanceof Observation ? ClinicalEncounterAssignment.optionResource(resource) : resource.fhirType();
        if (type == null) type = "Observation.other";
        if (type.equals("Location") || type.equals("Medication")) return mode(type).equals("generate-reference");
        return !BOOLEAN_TYPES.contains(type) || enabled("resource." + type + ".enabled");
    }

    public Resource output(Resource source) {
        if (!emits(source)) return null;
        if (configuration == null) return source;
        Resource output = source.copy();
        if (output instanceof Encounter) {
            var encounter = (Encounter)output;
            encounter.getLocation().removeIf(location -> {
                Reference reference = reference(location.getLocation(), "Location");
                if (reference == null) return true;
                location.setLocation(reference);
                return false;
            });
        }
        Type medication = medication(output);
        if (medication != null && !mode("Medication").equals("generate-reference")) {
            Type value = medication instanceof Reference ? reference((Reference)medication, "Medication")
                    : mode("Medication").equals("neither") ? null : medication;
            if (output instanceof MedicationRequest) ((MedicationRequest)output).setMedication(value);
            if (output instanceof MedicationAdministration) ((MedicationAdministration)output).setMedication(value);
            if (output instanceof MedicationStatement) ((MedicationStatement)output).setMedication(value);
        }
        if (!omittedTargets.isEmpty()) removeOmittedTargets(output);
        if (output instanceof Encounter) ((Encounter)output).getDiagnosis().removeIf(d -> !d.hasCondition());
        return output;
    }

    private Reference reference(Reference original, String type) {
        String mode = mode(type);
        if (mode.equals("generate-reference")) return original;
        if (mode.equals("neither")) return null;
        boolean literal = enabled("resource." + type + ".existingReference");
        boolean description = enabled("resource." + type + ".description");
        if (!literal && !description) return null;
        Reference output = new Reference();
        if (literal && original.hasReference()) output.setReference(original.getReference());
        if (description) {
            Resource target = potential.get(key(original));
            if (original.hasIdentifier()) output.setIdentifier(original.getIdentifier().copy());
            else if (target instanceof Medication && ((Medication)target).hasIdentifier())
                output.setIdentifier(((Medication)target).getIdentifierFirstRep().copy());
            else if (target instanceof Location && ((Location)target).hasIdentifier())
                output.setIdentifier(((Location)target).getIdentifierFirstRep().copy());
            else if (target != null && target.hasId()) output.setIdentifier(new Identifier().setValue(target.getIdElement().getIdPart()));
            if (original.hasDisplay()) output.setDisplay(original.getDisplay());
            else if (target instanceof Location && ((Location)target).hasName()) output.setDisplay(((Location)target).getName());
            else if (target instanceof Medication) {
                CodeableConcept code = ((Medication)target).getCode();
                if (code.hasText()) output.setDisplay(code.getText());
                else code.getCoding().stream().filter(Coding::hasDisplay).findFirst().ifPresent(c -> output.setDisplay(c.getDisplay()));
            }
        }
        return output.isEmpty() ? null : output;
    }

    /** Remove references to known deselected outputs, retaining explicit reference-only targets. */
    private void removeOmittedTargets(Base base) {
        for (Property property : base.children()) {
            for (Base value : new ArrayList<>(property.getValues())) {
                if (value instanceof Reference) {
                    if (omittedTargets.contains(key((Reference)value))) base.removeChild(property.getName(), value);
                } else if (!(value instanceof PrimitiveType<?>)) removeOmittedTargets(value);
            }
        }
    }
    private boolean referenceOnly(Resource resource) {
        return (resource instanceof Medication || resource instanceof Location) && mode(resource.fhirType()).equals("reference-only");
    }
    private static String key(Reference reference) {
        if (!reference.hasReference()) return "";
        var id = reference.getReferenceElement();
        // External absolute references are not rewritten merely because their IDs coincide.
        if (id.hasBaseUrl()) return "";
        return id.getResourceType() + "/" + id.getIdPart();
    }
    private static Type medication(Resource resource) {
        if (resource instanceof MedicationRequest) return ((MedicationRequest)resource).getMedication();
        if (resource instanceof MedicationAdministration) return ((MedicationAdministration)resource).getMedication();
        if (resource instanceof MedicationStatement) return ((MedicationStatement)resource).getMedication();
        return null;
    }
    private String mode(String type) { return configuration.stored("resource." + type + ".mode").asText(); }
    private boolean enabled(String id) { return configuration.effective(id).map(v -> v.asBoolean()).orElse(false); }
}
