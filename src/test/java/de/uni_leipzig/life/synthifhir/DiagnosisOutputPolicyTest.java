package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import static de.uni_leipzig.life.synthifhir.ContactIndex.Level.*;
import java.util.ArrayList;
import java.util.List;
import org.hl7.fhir.r4.model.*;
import org.junit.Test;

public class DiagnosisOutputPolicyTest {
    private static final String SYSTEM = "http://terminology.hl7.org/CodeSystem/diagnosis-role";
    private final ContactIndex index = new ContactIndex();
    private final List<Resource> resources = new ArrayList<>();
    private Encounter contact(String id, ContactIndex.Level level, String parent, long row, String start, String end) {
        Encounter e = new Encounter(); e.setId(id);
        if (start != null) e.getPeriod().setStartElement(new DateTimeType(start));
        if (end != null) e.getPeriod().setEndElement(new DateTimeType(end));
        index.add(e, "prefixed-patient", "case", level, parent, row, false);
        resources.add(e); return e;
    }
    private Condition diagnosis(Encounter e, String id, String role, String time) {
        Condition c = new Condition(); c.setId(id);
        if (time != null) c.setRecordedDateElement(new DateTimeType(time));
        resources.add(c);
        e.addDiagnosis().setCondition(new Reference("Condition/" + id))
                .setUse(new CodeableConcept(new Coding(SYSTEM, role, null)));
        return c;
    }
    private DiagnosisOutputPolicy policy(String settings) {
        return new DiagnosisOutputPolicy(ContractConfiguration.parse("CONFIGURATION_VERSION=1\n" + settings), index, resources);
    }
    private List<String> refs(DiagnosisOutputPolicy policy, Encounter e) {
        return ((Encounter)policy.output(e)).getDiagnosis().stream().map(d -> d.getCondition().getReference()).toList();
    }
    @Test public void unrestrictedRolesPreserveUnclassifiedLinksAtEveryAssignedLevel() {
        var root = contact("root", FACILITY, null, 1, "2026-01-01", "2026-01-10");
        var department = contact("department", DEPARTMENT, "root", 2, "2026-01-01", "2026-01-10");
        var ward = contact("ward", WARD_SERVICE, "department", 3, "2026-01-01", "2026-01-10");
        diagnosis(department, "missing", null, "2026-01-03");
        department.getDiagnosisFirstRep().setUse(null);
        diagnosis(department, "unknown", "custom-role", "2026-01-03");
        diagnosis(department, "foreign", "CC", "2026-01-03");
        department.getDiagnosis().get(2).getUse().getCodingFirstRep().setSystem("urn:custom");
        for (String roles : List.of("", "CONTACT_DIAGNOSES_ROLES=billing,post-op,pre-op,DD,AD,CM,CC\n")) {
            var p = policy("CONTACT_DIAGNOSES_LEVELS=facility,department,ward-service\n" + roles);
            for (var target : List.of(root, department, ward)) {
                assertEquals(List.of("Condition/missing", "Condition/unknown", "Condition/foreign"), refs(p, target));
                var output = (Encounter)p.output(target);
                assertFalse(output.getDiagnosisFirstRep().hasUse());
                assertEquals("custom-role", output.getDiagnosis().get(1).getUse().getCodingFirstRep().getCode());
                assertEquals("urn:custom", output.getDiagnosis().get(2).getUse().getCodingFirstRep().getSystem());
            }
            assertTrue(p.issues().isEmpty());
        }
        for (String roles : List.of("CC", "CM", "CC,CM", "AD", "")) {
            var p = policy("CONTACT_DIAGNOSES_LEVELS=facility,department,ward-service\nCONTACT_DIAGNOSES_ROLES=" + roles + "\n");
            for (var target : List.of(root, department, ward)) assertTrue(refs(p, target).isEmpty());
            for (var resource : resources) if (resource instanceof Condition) assertSame(resource, p.output(resource));
        }
        assertFalse(department.getDiagnosisFirstRep().hasUse());
    }

    @Test public void targetedConditionRolesDoNotFilterProcedureLinks() {
        var root = contact("root", FACILITY, null, 1, null, null);
        diagnosis(root, "chief", "CC", null);
        diagnosis(root, "secondary", "CM", null);
        var procedure = new Procedure(); procedure.setId("p"); resources.add(procedure);
        root.addDiagnosis().setCondition(new Reference("Procedure/p"));
        assertEquals(List.of("Condition/secondary", "Procedure/p"),
                refs(policy("CONTACT_DIAGNOSES_ROLES=CM\n"), root));
        assertEquals(List.of("Procedure/p"), refs(policy("CONTACT_DIAGNOSES_ROLES=\n"), root));
    }

    @Test public void downwardAssignmentChoosesOneDescendantPerLevelAndRespectsRoles() {
        var root = contact("root", FACILITY, null, 1, null, null);
        var old = contact("old", DEPARTMENT, "root", 2, "2026-05-01", "2026-05-03");
        var laterRow = contact("later-row", DEPARTMENT, "root", 5, "2026-05-02", null);
        var winner = contact("winner", DEPARTMENT, "root", 4, "2026-05-02", null);
        var ward = contact("ward", WARD_SERVICE, "winner", 6, "2026-05-02", null);
        contact("other-root", FACILITY, null, 7, null, null);
        var wrong = contact("wrong-case", DEPARTMENT, "other-root", 8, "2026-05-03", null);
        diagnosis(root, "a", "CC", "2026-05-03");
        diagnosis(root, "b", "CM", "2026-05-03");
        var p = policy("CONTACT_DIAGNOSES_LEVELS=department,ward-service\nCONTACT_DIAGNOSES_ROLES=CC\n");
        assertEquals(List.of("Condition/a"), refs(p, winner));
        assertEquals(List.of("Condition/a"), refs(p, ward));
        for (var e : List.of(root, old, laterRow, wrong)) assertTrue(refs(p, e).isEmpty());
        assertTrue(p.issues().isEmpty());
        assertEquals(2, root.getDiagnosis().size());
        assertEquals(0, winner.getDiagnosis().size());
    }
    @Test public void upwardAssignmentNeedsNoDateAndRetainsOwnRoleWithoutDuplicates() {
        var root = contact("root", FACILITY, null, 1, null, null);
        var department = contact("department", DEPARTMENT, "root", 2, null, null);
        var ward = contact("ward", WARD_SERVICE, "department", 3, null, null);
        diagnosis(ward, "a", "CC", null);
        department.addDiagnosis().setCondition(new Reference("Condition/a"))
                .setUse(new CodeableConcept(new Coding(SYSTEM, "CM", null)));
        var p = policy("CONTACT_DIAGNOSES_LEVELS=facility,department\n");
        assertEquals(List.of("Condition/a"), refs(p, root));
        assertEquals(List.of("Condition/a"), refs(p, department));
        assertEquals("CM", ((Encounter)p.output(department)).getDiagnosisFirstRep().getUse().getCodingFirstRep().getCode());
        assertTrue(refs(p, ward).isEmpty());
        assertTrue(p.issues().isEmpty());
    }
    @Test public void downwardSearchStaysInSourceBranchAndReportsMissingAssignments() {
        contact("root", FACILITY, null, 1, null, null);
        var department = contact("department", DEPARTMENT, "root", 2, null, null);
        contact("sibling", DEPARTMENT, "root", 3, null, null);
        var otherWard = contact("other-ward", WARD_SERVICE, "sibling", 4, "2026-05-01", null);
        diagnosis(department, "a", "CC", "2026-05-02");
        diagnosis(department, "b", "CC", null);
        var p = policy("CONTACT_DIAGNOSES_LEVELS=ward-service\n");
        assertTrue(refs(p, otherWard).isEmpty());
        assertEquals(2, p.issues().size());
        assertEquals("no-matching-contact", p.issues().get(0).get("reason"));
        assertEquals("missing-documentation-time", p.issues().get(1).get("reason"));
        assertEquals("department", p.issues().get(0).get("sourceContact"));
        assertEquals("ward-service", p.issues().get(0).get("targetLevel"));
    }
    @Test public void disabledConditionsRemoveResourcesAndReferencesButKeepProcedures() {
        var root = contact("root", FACILITY, null, 1, null, null);
        var c = diagnosis(root, "a", "CC", null);
        root.addDiagnosis().setCondition(new Reference("Procedure/p"));
        var procedure = new Procedure(); procedure.setId("p"); resources.add(procedure);
        var p = policy("CONDITION_ENABLED=false\n");
        assertNull(p.output(c));
        assertEquals(List.of("Procedure/p"), refs(p, root));
        assertTrue(p.issues().isEmpty());
        assertEquals(2, root.getDiagnosis().size());
    }
    @Test public void noRolesOrDisabledTargetProducesNoAssignmentsOrWarnings() {
        var root = contact("root", FACILITY, null, 1, null, null);
        diagnosis(root, "a", "CC", null);
        var p = policy("CONTACT_DIAGNOSES_LEVELS=department\nCONTACT_DEPARTMENT_ENABLED=false\n");
        assertTrue(p.issues().isEmpty());
        p = policy("CONTACT_DIAGNOSES_ROLES=\n");
        assertTrue(refs(p, root).isEmpty());
        assertTrue(p.issues().isEmpty());
    }
    @Test public void proceduresUseSelectedDescendantLevelAndOwnEnablement() {
        var root = contact("root", FACILITY, null, 1, "2026-01-01", null);
        var department = contact("department", DEPARTMENT, "root", 2, "2026-01-01", null);
        var wrong = contact("wrong", DEPARTMENT, "other-root", 3, "2026-01-02", null);
        var procedure = new Procedure(); procedure.setId("p");
        procedure.setPerformed(new Period().setStartElement(new DateTimeType("2026-01-03")));
        resources.add(procedure); root.addDiagnosis().setCondition(new Reference("Procedure/p"));
        var p = policy("CONDITION_ENABLED=false\nCONTACT_PROCEDURE_DIAGNOSES_LEVELS=department\n");
        assertEquals(List.of("Procedure/p"), refs(p, department));
        assertTrue(refs(p, root).isEmpty()); assertTrue(refs(p, wrong).isEmpty());
        p = policy("PROCEDURE_ENABLED=false\nCONTACT_PROCEDURE_DIAGNOSES_LEVELS=department\n");
        assertTrue(refs(p, department).isEmpty());
        assertTrue(refs(p, root).isEmpty());
    }
}
