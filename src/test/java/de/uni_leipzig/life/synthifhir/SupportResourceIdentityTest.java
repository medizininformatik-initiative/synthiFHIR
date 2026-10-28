package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.util.*;
import org.hl7.fhir.r4.model.*;
import org.junit.Test;

public class SupportResourceIdentityTest {
    private ConverterOptions options(String text) {
        return ConverterOptions.fromText("CONFIGURATION_VERSION=1\n" + text);
    }

    @Test public void facilityBoundariesAndCompletePathsAreUnambiguous() {
        var a = options("PID_PREFIX=ab\nPID_SUFFIX=c\n");
        var b = options("PID_PREFIX=a\nPID_SUFFIX=bc\n");
        String path = SupportResourceIdentity.key("Department", "wa", "A", "ro", "101", "bd", "Bed 1");
        String id = SupportResourceIdentity.place(a, "Location", path, "patient-a-encounter");
        assertEquals(id, SupportResourceIdentity.place(a, "Location", path, "patient-b-encounter"));
        assertNotEquals(id, SupportResourceIdentity.place(b, "Location", path, "patient-a-encounter"));
        for (String other : List.of(
                SupportResourceIdentity.key("Department", "wa", "A", "ro", "102", "bd", "Bed 1"),
                SupportResourceIdentity.key("Department", "wa", "B", "ro", "101", "bd", "Bed 1"),
                SupportResourceIdentity.key("Other", "wa", "A", "ro", "101", "bd", "Bed 1")))
            assertNotEquals(id, SupportResourceIdentity.place(a, "Location", other, "patient-a-encounter"));
        assertNotEquals(SupportResourceIdentity.key("A|wa|B", "C"), SupportResourceIdentity.key("A", "wa|B|C"));
        var separate = options("LOCATION_IDENTITY=separate\n");
        assertNotEquals(SupportResourceIdentity.place(separate, "Location", path, "a"),
                SupportResourceIdentity.place(separate, "Location", path, "b"));
        assertEquals(SupportResourceIdentity.place(separate, "Organization", path, "a"),
                SupportResourceIdentity.place(separate, "Organization", path, "b"));
    }

    @Test public void separateProductsPreserveEventsAndCloneIngredientGraphs() throws Exception {
        Medication product = new Medication(); product.setId("product");
        product.addIngredient().setItem(new Reference("Medication/ingredient"));
        Medication ingredient = new Medication(); ingredient.setId("ingredient");
        ingredient.setCode(new CodeableConcept().setText("Ingredient"));
        MedicationRequest request = new MedicationRequest(); request.setId("request");
        request.setMedication(new Reference("Medication/product"));
        request.addDosageInstruction().setText("Keep this regimen");
        MedicationAdministration administration = new MedicationAdministration(); administration.setId("administration");
        administration.setMedication(new Reference("Medication/product"));
        administration.getDosage().setDose(new Quantity().setValue(2).setUnit("mg"));
        var input = List.<Resource>of(product, ingredient, request, administration);
        var separate = options("MEDICATION_IDENTITY=separate\n");
        var output = SupportResourceIdentity.medications(input, new ConverterResult(separate));
        var repeated = SupportResourceIdentity.medications(input, new ConverterResult(separate));
        assertEquals(6, output.size());
        assertEquals(output.stream().map(Resource::getId).toList(), repeated.stream().map(Resource::getId).toList());
        assertEquals(4, output.stream().filter(r -> r instanceof Medication).count());
        Set<String> ids = new HashSet<>();
        output.forEach(r -> ids.add(r.fhirType() + "/" + r.getIdElement().getIdPart()));
        for (Resource resource : output) if (resource instanceof Medication)
            for (var part : ((Medication)resource).getIngredient())
                if (part.getItem() instanceof Reference) assertTrue(ids.contains(((Reference)part.getItem()).getReference()));
        var r = (MedicationRequest)output.stream().filter(x -> x instanceof MedicationRequest).findFirst().orElseThrow();
        var a = (MedicationAdministration)output.stream().filter(x -> x instanceof MedicationAdministration).findFirst().orElseThrow();
        assertNotEquals(r.getMedicationReference().getReference(), a.getMedicationReference().getReference());
        assertTrue(ids.contains(r.getMedicationReference().getReference()));
        assertTrue(ids.contains(a.getMedicationReference().getReference()));
        r.setMedication(request.getMedication().copy()); a.setMedication(administration.getMedication().copy());
        assertTrue(request.equalsDeep(r)); assertTrue(administration.equalsDeep(a));
        assertEquals("Medication/ingredient", product.getIngredientFirstRep().getItemReference().getReference());
        assertSame(input, SupportResourceIdentity.medications(input, new ConverterResult(options(""))));
    }
}
