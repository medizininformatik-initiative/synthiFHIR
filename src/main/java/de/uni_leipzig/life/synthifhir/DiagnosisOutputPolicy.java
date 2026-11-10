package de.uni_leipzig.life.synthifhir;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import org.hl7.fhir.r4.model.Condition;
import org.hl7.fhir.r4.model.Encounter;
import org.hl7.fhir.r4.model.Encounter.DiagnosisComponent;
import org.hl7.fhir.r4.model.Resource;
import org.hl7.fhir.r4.model.Procedure;
import org.hl7.fhir.r4.model.Period;
import org.hl7.fhir.r4.model.DateTimeType;

/** Assigns diagnosis references using original contacts before projecting output. */
public final class DiagnosisOutputPolicy {
    private static final String ROLE_SYSTEM = "http://terminology.hl7.org/CodeSystem/diagnosis-role";
    private final ContractConfiguration configuration;
    private final Map<String, Map<String, DiagnosisComponent>> assigned = new LinkedHashMap<>();
    private final List<Map<String, String>> issues = new ArrayList<>();

    public DiagnosisOutputPolicy(ContractConfiguration configuration, ContactIndex contacts,
            List<Resource> resources) {
        this.configuration = configuration;
        if (configuration == null) return;
        Set<String> roles = selected(configuration, "contact.diagnoses.roles");
        boolean unrestrictedRoles = configuration.allChoicesSelected("contact.diagnoses.roles");
        Map<String, Resource> conditions = new LinkedHashMap<>();
        for (Resource resource : resources)
            if (resource instanceof Condition || resource instanceof Procedure)
                conditions.put(resource.fhirType() + "/" + resource.getIdElement().getIdPart(), resource);
        Set<String> processed = new HashSet<>();
        for (Resource resource : resources) {
            if (!(resource instanceof Encounter) || !processed.add(resource.getId())) continue;
            Encounter encounter = (Encounter)resource;
            for (DiagnosisComponent diagnosis : encounter.getDiagnosis()) {
                boolean procedure = isProcedure(diagnosis);
                if (!procedure && !isCondition(diagnosis)) continue;
                String group = procedure ? "contact.procedureDiagnoses" : "contact.diagnoses";
                if (!configuration.effective(group + ".enabled").map(v -> v.asBoolean()).orElse(false)) continue;
                if (!procedure && !unrestrictedRoles && (!diagnosis.hasUse() || diagnosis.getUse().getCoding().stream()
                        .noneMatch(c -> ROLE_SYSTEM.equals(c.getSystem()) && roles.contains(c.getCode())))) continue;
                Set<String> levels = selected(configuration, group + ".levels");
                String id = diagnosis.getCondition().getReferenceElement().getIdPart();
                String key = (procedure ? "Procedure/" : "Condition/") + id;
                Resource condition = conditions.get(key);
                for (ContactIndex.Level level : ContactIndex.Level.values()) {
                    String name = levelName(level);
                    if (!levels.contains(name) || !configuration.effective("contact." + name + ".enabled")
                            .map(v -> v.asBoolean()).orElse(false)) continue;
                    if (condition == null) { issue(id, encounter, name, "missing-condition"); continue; }
                    var source = contacts.get(encounter);
                    if (source.isEmpty()) { issue(id, encounter, name, "missing-source-contact"); continue; }
                    Optional<ContactIndex.Entry> target;
                    if (level.ordinal() <= source.get().level().ordinal()) {
                        target = contacts.ancestor(source.get().patientId(), encounter.getId(), level);
                    } else {
                        DateTimeType timestamp = timestamp(condition);
                        if (timestamp == null || !timestamp.hasValue()) {
                            issue(id, encounter, name, "missing-documentation-time");
                            continue;
                        }
                        target = contacts.matchDescendant(source.get(), level, timestamp);
                    }
                    if (target.isEmpty()) { issue(id, encounter, name, "no-matching-contact"); continue; }
                    Map<String, DiagnosisComponent> references = assigned.computeIfAbsent(
                            target.get().encounter().getId(), unused -> new LinkedHashMap<>());
                    // A target's own role takes precedence over an automatically transferred role.
                    if (target.get().encounter().getId().equals(encounter.getId())) references.put(key, diagnosis);
                    else references.putIfAbsent(key, diagnosis);
                }
            }
        }
    }

    public Resource output(Resource source) {
        if (configuration == null) return source;
        if (source instanceof Condition)
            return configuration.stored("resource.Condition.enabled").asBoolean() ? source : null;
        if (!(source instanceof Encounter)) return source;
        Encounter output = ((Encounter)source).copy();
        output.getDiagnosis().removeIf(d -> isCondition(d) || isProcedure(d));
        assigned.getOrDefault(source.getId(), Map.of()).values().forEach(d -> output.addDiagnosis(d.copy()));
        return output;
    }

    public List<Map<String, String>> issues() { return List.copyOf(issues); }

    private void issue(String condition, Encounter source, String level, String reason) {
        issues.add(Map.of("conditionId", condition == null ? "" : condition, "sourceContact", source.getId(),
                "targetLevel", level, "reason", reason));
    }

    static boolean isCondition(DiagnosisComponent diagnosis) {
        var reference = diagnosis.getCondition();
        return "Condition".equals(reference.getType())
                || "Condition".equals(reference.getReferenceElement().getResourceType())
                || reference.getResource() instanceof Condition;
    }

    private static boolean isProcedure(DiagnosisComponent diagnosis) {
        var reference = diagnosis.getCondition();
        return "Procedure".equals(reference.getType())
                || "Procedure".equals(reference.getReferenceElement().getResourceType())
                || reference.getResource() instanceof Procedure;
    }

    private static DateTimeType timestamp(Resource resource) {
        if (resource instanceof Condition) return ((Condition)resource).getRecordedDateElement();
        var performed = ((Procedure)resource).getPerformed();
        if (performed instanceof DateTimeType) return (DateTimeType)performed;
        return performed instanceof Period ? ((Period)performed).getStartElement() : null;
    }

    private static Set<String> selected(ContractConfiguration configuration, String id) {
        Set<String> values = new HashSet<>();
        configuration.effective(id).ifPresent(value -> value.forEach(item -> values.add(item.asText())));
        return values;
    }
    private static String levelName(ContactIndex.Level level) {
        switch (level) {
        case FACILITY: return "facility";
        case DEPARTMENT: return "department";
        default: return "ward-service";
        }
    }
}
