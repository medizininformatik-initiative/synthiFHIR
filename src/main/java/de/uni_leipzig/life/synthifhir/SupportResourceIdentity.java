package de.uni_leipzig.life.synthifhir;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.hl7.fhir.r4.model.*;

/** Deterministic support-resource identities and event-local medication graphs. */
public final class SupportResourceIdentity {
    private SupportResourceIdentity() { }

    public static boolean supports(String option) {
        return Set.of("resource.Organization.identity", "resource.Location.identity", "resource.Medication.identity").contains(option);
    }

    public static boolean separate(ConverterOptions options, String type) {
        var configuration = options.configuration();
        return configuration != null && configuration.stored("resource." + type + ".identity").asText().equals("separate");
    }

    /** Length prefixes preserve boundaries even when labels contain separators. */
    public static String key(String... parts) {
        StringBuilder key = new StringBuilder();
        for (String part : parts) {
            String value = Objects.toString(part, "");
            key.append(value.length()).append(':').append(value);
        }
        return key.toString();
    }

    public static String facility(ConverterOptions options) {
        return key(options.getValue(ConverterOptions.StringOption.PID_PREFIX),
                options.getValue(ConverterOptions.StringOption.PID_SUFFIX));
    }

    public static String id(String type, String key) {
        return type + "-" + UUID.nameUUIDFromBytes(key.getBytes(StandardCharsets.UTF_8));
    }

    public static String place(ConverterOptions options, String type, String path, String encounter) {
        return id(type, key(facility(options), type, path, separate(options, type) ? encounter : ""));
    }

    /** Run after event transformations so added events also receive their own product graph. */
    public static List<Resource> medications(List<Resource> resources, ConverterResult context) {
        ConverterOptions options = context.getConverterOptions();
        if (!separate(options, "Medication")) return resources;
        Map<String, Medication> products = new LinkedHashMap<>();
        for (Resource resource : resources) if (resource instanceof Medication)
            products.put("Medication/" + resource.getIdElement().getIdPart(), (Medication)resource);
        List<Resource> output = new ArrayList<>();
        Set<String> copied = new HashSet<>();
        for (Resource resource : resources) {
            if (resource instanceof Medication) continue;
            Type medication = resource instanceof MedicationRequest ? ((MedicationRequest)resource).getMedication()
                    : resource instanceof MedicationAdministration ? ((MedicationAdministration)resource).getMedication()
                    : resource instanceof MedicationStatement ? ((MedicationStatement)resource).getMedication() : null;
            if (!(medication instanceof Reference) || !products.containsKey(((Reference)medication).getReference())) {
                output.add(resource);
                continue;
            }
            String event = resource.fhirType() + "/" + resource.getIdElement().getIdPart();
            Map<String, Medication> graph = new LinkedHashMap<>();
            String target = copyProduct(((Reference)medication).getReference(), event, products, graph, copied);
            Resource copy = resource.copy();
            Reference reference = ((Reference)medication).copy().setReference(target);
            if (copy instanceof MedicationRequest) ((MedicationRequest)copy).setMedication(reference);
            else if (copy instanceof MedicationAdministration) ((MedicationAdministration)copy).setMedication(reference);
            else ((MedicationStatement)copy).setMedication(reference);
            var input = context.inputContext(resource);
            if (input != null) graph.values().forEach(product -> context.recordInput(product, input));
            output.addAll(graph.values());
            output.add(copy);
        }
        // Preserve definitions that were not consumed by any event.
        Set<String> retained = new HashSet<>();
        for (String reference : products.keySet()) if (!copied.contains(reference))
            retainProduct(reference, products, retained, output);
        return output;
    }

    private static void retainProduct(String reference, Map<String, Medication> products,
            Set<String> retained, List<Resource> output) {
        if (!retained.add(reference)) return;
        Medication product = products.get(reference);
        output.add(product);
        for (var ingredient : product.getIngredient()) if (ingredient.getItem() instanceof Reference) {
            String target = ((Reference)ingredient.getItem()).getReference();
            if (products.containsKey(target)) retainProduct(target, products, retained, output);
        }
    }

    private static String copyProduct(String reference, String event, Map<String, Medication> products,
            Map<String, Medication> graph, Set<String> copied) {
        if (graph.containsKey(reference)) return "Medication/" + graph.get(reference).getIdElement().getIdPart();
        Medication product = products.get(reference).copy();
        product.setId(id("Medication", key(reference, event)));
        graph.put(reference, product); // Register before traversing references, including cycles.
        copied.add(reference);
        for (var ingredient : product.getIngredient()) {
            if (ingredient.getItem() instanceof Reference) {
                Reference target = (Reference)ingredient.getItem();
                if (products.containsKey(target.getReference()))
                    target.setReference(copyProduct(target.getReference(), event, products, graph, copied));
            }
        }
        return "Medication/" + product.getIdElement().getIdPart();
    }
}
