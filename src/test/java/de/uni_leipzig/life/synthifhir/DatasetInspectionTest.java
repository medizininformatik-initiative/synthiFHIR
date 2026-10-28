package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.util.zip.GZIPOutputStream;
import java.util.zip.ZipOutputStream;
import java.util.zip.ZipEntry;
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorOutputStream;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class DatasetInspectionTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();
    private static final String BUNDLE = "{\"resourceType\":\"Bundle\",\"entry\":[{\"resource\":{\"resourceType\":\"Patient\",\"id\":\"p1\"}},{\"resource\":{\"resourceType\":\"Medication\",\"id\":\"m1\"}}]}";

    @Test public void countsOneFormatPerFolderAndUniqueIdsAcrossPatientFiles() throws Exception {
        Path root = temporary.newFolder().toPath();
        Files.writeString(root.resolve("one.json"), BUNDLE);
        Files.writeString(root.resolve("two.json"), BUNDLE.replace("p1", "p2"));
        Files.writeString(root.resolve("patients.ndjson"), BUNDLE + "\n" + BUNDLE);
        var report = DatasetInspection.inspect(root);
        assertEquals(2, report.get("patients").asInt());
        assertEquals(3, report.get("uniqueResources").asInt());
        assertEquals(4, report.get("resourceInstances").asInt());
        assertEquals(1, report.get("repeatedIds").asInt());
        assertEquals(2, report.get("inspectedFiles").asInt());
    }

    @Test public void supportsCompressedJsonNdjsonAndXmlWithoutDoubleCounting() throws Exception {
        Path root = temporary.newFolder().toPath();
        byte[] json = BUNDLE.getBytes(StandardCharsets.UTF_8);
        try (var out = new GZIPOutputStream(Files.newOutputStream(root.resolve("p.json.gz")))) { out.write(json); }
        assertEquals(1, DatasetInspection.inspect(root).get("patients").asInt());
        Files.delete(root.resolve("p.json.gz"));
        try (var out = new BZip2CompressorOutputStream(Files.newOutputStream(root.resolve("p.json.bz2")))) { out.write(json); }
        assertEquals(1, DatasetInspection.inspect(root).get("patients").asInt());
        Files.delete(root.resolve("p.json.bz2"));
        try (var out = new ZipOutputStream(Files.newOutputStream(root.resolve("p.json.zip")))) {
            out.putNextEntry(new ZipEntry("p.json")); out.write(json); out.closeEntry();
        }
        assertEquals(1, DatasetInspection.inspect(root).get("patients").asInt());
        Files.delete(root.resolve("p.json.zip"));
        Files.writeString(root.resolve("patients.ndjson"), BUNDLE + "\n" + BUNDLE.replace("p1", "p2"));
        assertEquals(2, DatasetInspection.inspect(root).get("patients").asInt());
        Files.delete(root.resolve("patients.ndjson"));
        Files.writeString(root.resolve("p.xml"), "<Bundle xmlns=\"http://hl7.org/fhir\"><meta><tag><system value=\"ignored\"/></tag></meta><entry><resource><Patient><id value=\"p1\"/><contained><Observation><id value=\"nested\"/></Observation></contained></Patient></resource></entry></Bundle>");
        var report = DatasetInspection.inspect(root);
        assertEquals(1, report.get("uniqueResources").asInt());
        assertEquals(1, report.get("patients").asInt());
    }

    @Test public void statisticsAgreeAcrossFormatsAndKeepOccurrencesSeparateFromIds() throws Exception {
        Path root = temporary.newFolder().toPath();
        String encounter = "{\"resourceType\":\"Encounter\",\"id\":\"same\","
                + "\"class\":{\"system\":\"class-system\",\"code\":\"IMP\"},\"partOf\":{\"reference\":\"Encounter/parent\"},"
                + "\"type\":[{\"coding\":[{\"system\":\"http://fhir.de/CodeSystem/Kontaktebene\",\"code\":\"abteilungskontakt\"}]}]}";
        String patient = "{\"resourceType\":\"Patient\",\"id\":\"same\",\"contained\":[{\"resourceType\":\"Medication\",\"id\":\"hidden\"}]}";
        String bundle = "{\"resourceType\":\"Bundle\",\"entry\":[{\"resource\":" + patient
                + "},{\"resource\":" + encounter + "},{\"resource\":" + encounter
                + "},{\"resource\":{\"resourceType\":\"Location\"}}]}";
        String xmlEncounter = "<Encounter><id value=\"same\"/><class><system value=\"class-system\"/><code value=\"IMP\"/></class>"
                + "<partOf><reference value=\"Encounter/parent\"/></partOf><type><coding>"
                + "<system value=\"http://fhir.de/CodeSystem/Kontaktebene\"/><code value=\"abteilungskontakt\"/>"
                + "</coding></type></Encounter>";
        Files.writeString(root.resolve("one.json"), bundle);
        Files.writeString(root.resolve("one.ndjson"), bundle);
        Files.writeString(root.resolve("one.xml"), "<Bundle xmlns=\"http://hl7.org/fhir\"><entry><resource>"
                + "<Patient><id value=\"same\"/><contained><Medication><id value=\"hidden\"/></Medication></contained></Patient>"
                + "</resource></entry><entry><resource>" + xmlEncounter + "</resource></entry><entry><resource>"
                + xmlEncounter + "</resource></entry><entry><resource><Location/></resource></entry></Bundle>");
        var statistics = DatasetInspection.inspect(root).get("statistics");
        assertEquals(1, statistics.get("bundles").asInt());
        assertEquals(2, statistics.path("resources").path("Encounter").asInt());
        assertEquals(1, statistics.path("distinctIds").path("Encounter").asInt());
        assertEquals(1, statistics.path("distinctIds").path("Patient").asInt());
        assertEquals(0, statistics.path("distinctIds").path("Location").asInt());
        assertEquals(1, statistics.path("missingIds").path("Location").asInt());
        assertFalse(statistics.path("resources").has("Medication"));
        assertEquals("Sub-encounter", statistics.path("encounters").get(0).get(2).asText());
        assertEquals("Department contact", statistics.path("contacts").get(0).get(0).asText());
        Files.delete(root.resolve("one.json"));
        assertEquals(statistics, DatasetInspection.inspect(root).get("statistics"));
        Files.delete(root.resolve("one.ndjson"));
        assertEquals(statistics, DatasetInspection.inspect(root).get("statistics"));
    }
}
