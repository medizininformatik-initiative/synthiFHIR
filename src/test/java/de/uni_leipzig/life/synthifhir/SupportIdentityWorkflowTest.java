package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.nio.file.*;
import java.util.*;
import org.apache.commons.csv.*;
import org.hl7.fhir.r4.model.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;

/** Real CSV conversion verifies independently usable patient bundles under all policies. */
public class SupportIdentityWorkflowTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private Path input;

    private void table(TableIdentifier type, List<Map<String, String>> rows) throws Exception {
        Set<String> headers = new LinkedHashSet<>(type.getMandatoryColumnNames());
        rows.forEach(row -> headers.addAll(row.keySet()));
        try (var writer = Files.newBufferedWriter(input.resolve("case_" + type + ".csv"));
                var csv = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
            csv.printRecord(headers);
            for (var row : rows) csv.printRecord(headers.stream().map(h -> row.getOrDefault(h, "")).toList());
        }
    }

    @Before public void setup() throws Exception {
        input = temp.newFolder().toPath();
        var people = new ArrayList<Map<String, String>>();
        var stays = new ArrayList<Map<String, String>>();
        var medications = new ArrayList<Map<String, String>>();
        for (String patient : List.of("aaaa-1111", "bbbb-2222")) {
            people.add(Map.of("Patient-ID", patient, "Vorname", "Test", "Nachname", "Person", "Geburtsdatum", "2000-01-01", "Geschlecht", "weiblich"));
            for (String room : List.of("101", "102")) {
                Map<String, String> row = new LinkedHashMap<>(Map.of("Patient-ID", patient, "Fall-Nr", room,
                        "Start", "2026-01-01", "Ende", "2026-01-10", "Einrichtungskontaktklasse", "stationaer",
                        "Fachabteilung", "0100", "Station", "Ward A", "Zimmer", room, "Bett", "Bed 1"));
                stays.add(row);
            }
            for (String kind : List.of("Verordnung", "Verabreichung")) {
                Map<String, String> row = new LinkedHashMap<>(Map.of("Patient-ID", patient, "Fall-Nr", "101",
                        "Medikationstyp", kind, "Präparatcode", "00000000", "Präparatcodesystem", "PZN",
                        "Einzeldosis", "2", "Dosiereinheit", "mg"));
                if (kind.equals("Verabreichung")) { row.put("Beginn", "2026-01-02"); row.put("Status", "completed"); }
                medications.add(row);
            }
        }
        table(TableIdentifier.Person, people); table(TableIdentifier.Fall, stays); table(TableIdentifier.Medikation, medications);
    }

    private List<Bundle> run(String settings) throws Exception {
        Path output = temp.newFolder().toPath();
        var converter = new Csv2Fhir(input.toFile(), output.toFile(), "case_", null,
                ConverterOptions.fromText("CONFIGURATION_VERSION=1\nOUTPUT_PATIENTS_PER_FILE=1\n" + settings));
        converter.convertFiles(Integer.MAX_VALUE, OutputFileType.NDJSON);
        assertFalse(converter.getImportReport().issues.toString(), converter.getImportReport().hasErrors());
        List<Bundle> bundles = new ArrayList<>();
        try (var files = Files.walk(output)) {
            for (Path file : files.filter(p -> p.toString().endsWith(".ndjson")).sorted().toList())
                for (String line : Files.readAllLines(file)) bundles.add((Bundle)OutputFileType.JSON.getParser().parseResource(line));
        }
        assertEquals(2, bundles.size());
        return bundles;
    }

    private Set<String> ids(Bundle bundle, String type) {
        Set<String> ids = new HashSet<>();
        for (var entry : bundle.getEntry()) if (entry.getResource().fhirType().equals(type))
            assertTrue("Duplicate transaction entry", ids.add(entry.getResource().getIdElement().getIdPart()));
        return ids;
    }

    private void references(Base base, Set<String> ids) {
        if (base instanceof Reference) {
            String reference = ((Reference)base).getReference();
            if (reference != null && !reference.startsWith("#") && !reference.contains("://")) assertTrue(reference, ids.contains(reference));
        } else for (Property property : base.children()) for (Base child : property.getValues())
            if (!(child instanceof PrimitiveType<?>)) references(child, ids);
    }

    @Test public void allIndependentModesPreserveHierarchyEventsAndBundleClosure() throws Exception {
        for (String org : List.of("shared", "separate")) for (String location : List.of("shared", "separate"))
            for (String medication : List.of("shared", "separate")) {
                var bundles = run("ORGANIZATION_IDENTITY=" + org + "\nLOCATION_IDENTITY=" + location + "\nMEDICATION_IDENTITY=" + medication + "\n");
                for (Bundle bundle : bundles) {
                    assertEquals(org.equals("shared") ? 1 : 2, ids(bundle, "Organization").size());
                    assertEquals(location.equals("shared") ? 5 : 6, ids(bundle, "Location").size());
                    assertEquals(medication.equals("shared") ? 1 : 2, ids(bundle, "Medication").size());
                    assertEquals(1, ids(bundle, "MedicationRequest").size());
                    assertEquals(1, ids(bundle, "MedicationAdministration").size());
                    Set<String> all = new HashSet<>();
                    bundle.getEntry().forEach(e -> all.add(e.getResource().fhirType() + "/" + e.getResource().getIdElement().getIdPart()));
                    bundle.getEntry().forEach(e -> references(e.getResource(), all));
                    List<Location> beds = bundle.getEntry().stream().map(Bundle.BundleEntryComponent::getResource)
                            .filter(r -> r instanceof Location && ((Location)r).getName().equals("Bed 1")).map(r -> (Location)r).toList();
                    assertEquals(2, beds.size());
                    assertNotEquals(beds.get(0).getPartOf().getReference(), beds.get(1).getPartOf().getReference());
                }
                for (var entry : Map.of("Organization", org, "Location", location, "Medication", medication).entrySet()) {
                    var first = ids(bundles.get(0), entry.getKey()); var second = ids(bundles.get(1), entry.getKey());
                    if (entry.getValue().equals("shared")) assertEquals(first, second);
                    else assertTrue(Collections.disjoint(first, second));
                }
            }
    }

    @Test public void repeatedRunsAndDerivedMedicationEventsHaveStableIdentities() throws Exception {
        String settings = "MEDICATION_IDENTITY=separate\nMEDICATION_ADMINISTRATION_TREATMENT=add-statement\n";
        var first = run(settings); var second = run(settings);
        for (int i = 0; i < first.size(); i++) {
            assertEquals(3, ids(first.get(i), "Medication").size());
            assertEquals(1, ids(first.get(i), "MedicationStatement").size());
            for (String type : List.of("Organization", "Location", "Medication")) assertEquals(ids(first.get(i), type), ids(second.get(i), type));
        }
    }
    @Test public void parentOutputAndFacilityScopeParticipateInLocationIdentity() throws Exception {
        var full = run("").get(0);
        var noWard = run("ENCOUNTER_STATION_LOCATION=false\n").get(0);
        assertEquals(4, ids(noWard, "Location").size());
        assertTrue(Collections.disjoint(ids(full, "Location"), ids(noWard, "Location")));
        var otherFacility = run("PID_PREFIX=other-\n").get(0);
        for (String type : List.of("Organization", "Location"))
            assertTrue(Collections.disjoint(ids(full, type), ids(otherFacility, type)));
        assertEquals(ids(full, "Medication"), ids(otherFacility, "Medication"));
    }

    @Test public void separateProductsParticipateInIdentifierAllocation() throws Exception {
        var bundles = run("MEDICATION_IDENTITY=separate\nMEDICATION_ADMINISTRATION_TREATMENT=add-statement\n"
                + "IDENTIFIER_RULE_1_ID=a152e771-3d5a-4cb1-9866-35fa6d91fd83\n"
                + "IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=Medication\n"
                + "IDENTIFIER_RULE_1_SYSTEM=urn:test:product\nIDENTIFIER_RULE_1_PATTERN={patientId}/{resourceId}\n");
        for (Bundle bundle : bundles) for (var entry : bundle.getEntry())
            if (entry.getResource() instanceof Medication) {
                var product = (Medication)entry.getResource();
                String patient = ids(bundle, "Patient").iterator().next();
                assertEquals(patient + "/" + product.getIdElement().getIdPart(), product.getIdentifierFirstRep().getValue());
            }
    }

}
