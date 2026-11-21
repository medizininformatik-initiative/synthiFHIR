package de.uni_leipzig.imise;

import static org.junit.Assert.*;
import java.nio.file.*;
import java.util.*;
import com.google.gson.*;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import de.uni_leipzig.UcumMapper;

public class Excel2FhirBatchTest {
    @Rule public TemporaryFolder temporary = new TemporaryFolder();

    private Path execute(String name, Path input, Path config) throws Exception {
        return execute(name, input, config, null);
    }

    private Path execute(String name, Path input, Path config, IdentifierSequence sequence) throws Exception {
        Path root = temporary.newFolder(name).toPath();
        var job = new JsonObject();
        job.add("arguments", new Gson().toJsonTree(new String[]{"-f", input.toString(), "-o", root.toString(),
                "-p", "1000", "--converter-options", config.toString()}));
        job.addProperty("log", root.resolve("conversion.log").toString());
        job.addProperty("result", root.resolve("result.json").toString());
        UcumMapper.invalidUcumCodes.add("previous-case-warning");
        Excel2FhirBatch.execute(job, sequence);
        assertTrue(UcumMapper.invalidUcumCodes.isEmpty());
        return root;
    }

    private Map<String, String> outputs(Path root) throws Exception {
        Map<String, String> values = new TreeMap<>();
        try (var paths = Files.walk(root)) {
            for (Path p : paths.filter(Files::isRegularFile).filter(p -> p.toString().contains("/fhir/")).toList())
                values.put(p.getFileName().toString(), Files.readString(p));
        }
        assertFalse(values.isEmpty());
        return values;
    }

    @Test public void isolatesOptionsFailuresLogsAndResourceIdsBetweenCases() throws Exception {
        Path input = Path.of("FHIR_Testdatengenerator_Vorlage.xlsx");
        Path a = temporary.newFile("a.config").toPath(), b = temporary.newFile("b.config").toPath();
        Files.writeString(a, "CONFIGURATION_VERSION=1\nPID_PREFIX=first-\n");
        Files.writeString(b, "CONFIGURATION_VERSION=1\nPID_PREFIX=second-\n");
        Path first = execute("first", input, a);
        String originalLog = Files.readString(first.resolve("conversion.log"));
        var expected = outputs(first);
        Path failed = execute("failed", Path.of("missing-workbook.xlsx"), b);
        assertNotEquals(0, JsonParser.parseString(Files.readString(failed.resolve("result.json")))
                .getAsJsonObject().get("exitCode").getAsInt());
        Path second = execute("second", input, b);
        assertNotEquals(expected, outputs(second));
        Path repeated = execute("repeated", input, a);
        assertEquals(expected, outputs(repeated));
        assertEquals(originalLog, Files.readString(first.resolve("conversion.log")));
        assertFalse(originalLog.contains("previous-case-warning"));
        for (Path root : List.of(first, second, repeated))
            assertEquals(0, JsonParser.parseString(Files.readString(root.resolve("result.json")))
                    .getAsJsonObject().get("exitCode").getAsInt());
    }
    private void stripGeneratedIdentifiers(JsonElement element, Set<String> values) {
        if (element.isJsonArray()) {
            for (var item : element.getAsJsonArray()) stripGeneratedIdentifiers(item, values);
        } else if (element.isJsonObject()) {
            var object = element.getAsJsonObject();
            if (object.has("identifier") && object.get("identifier").isJsonArray()) {
                var identifiers = object.getAsJsonArray("identifier");
                for (int i = identifiers.size() - 1; i >= 0; i--) {
                    var identifier = identifiers.get(i).getAsJsonObject();
                    if (identifier.has("system") && identifier.get("system").getAsString().equals("urn:series-test")) {
                        values.add(identifier.get("value").getAsString());
                        identifiers.remove(i);
                    }
                }
            }
            for (var entry : object.entrySet()) stripGeneratedIdentifiers(entry.getValue(), values);
        }
    }

    @Test public void carriesCountersAcrossPatientBatchesWithoutChangingResourcesOrReferences() throws Exception {
        Path config = temporary.newFile("series.config").toPath();
        Files.writeString(config, "CONFIGURATION_VERSION=1\nOUTPUT_FORMATS=JSON\n"
                + "IDENTIFIER_RULE_1_ID=a152e771-3d5a-4cb1-9866-35fa6d91fd83\n"
                + "IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=Patient,Encounter\n"
                + "IDENTIFIER_RULE_1_SYSTEM=urn:series-test\nIDENTIFIER_RULE_1_PATTERN={count}\n");
        var registrations = new ArrayList<Map<String, String>>();
        var sequence = new IdentifierSequence(Map.of(), registrations::add);
        var first = outputs(execute("sequence-first", Path.of("FHIR_Testdatengenerator_Vorlage.xlsx"), config, sequence));
        var restored = new IdentifierSequence(sequence.counters(), registrations::add);
        var second = outputs(execute("sequence-second", Path.of("FHIR_Testdatengenerator_Vorlage.xlsx"), config, restored));
        assertEquals(first.keySet(), second.keySet());
        Set<String> firstValues = new HashSet<>(), secondValues = new HashSet<>();
        for (var name : first.keySet()) {
            var a = JsonParser.parseString(first.get(name));
            var b = JsonParser.parseString(second.get(name));
            stripGeneratedIdentifiers(a, firstValues);
            stripGeneratedIdentifiers(b, secondValues);
            assertEquals("Only generated identifier values may change", a, b);
        }
        assertFalse(firstValues.isEmpty());
        assertFalse(secondValues.isEmpty());
        assertTrue(Collections.disjoint(firstValues, secondValues));
        assertFalse(registrations.isEmpty());
    }

}
