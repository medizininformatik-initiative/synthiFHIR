package de.uni_leipzig.life.synthifhir;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.zip.GZIPInputStream;
import java.util.zip.ZipInputStream;
import javax.xml.stream.XMLInputFactory;
import javax.xml.stream.XMLStreamConstants;
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

/** Streams one representation per output folder and counts unique resource IDs. */
public final class DatasetInspection {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final List<String> SUFFIXES = List.of(".json", ".ndjson", ".json.gz", ".json.bz2", ".json.zip", ".xml");
    private final Map<String, Set<String>> ids = new TreeMap<>();
    private long instances, missingIds, files, bundles;
    private final Map<String, Long> occurrences = new TreeMap<>(), missingByType = new TreeMap<>();
    private final Map<List<String>, Long> encounters = new LinkedHashMap<>(), contacts = new LinkedHashMap<>();

    private void encounter(String system, String code, boolean partOf, Set<String> levels) {
        String hierarchy = partOf ? "Sub-encounter" : "Main encounter";
        encounters.merge(List.of(system, code, hierarchy), 1L, Long::sum);
        Map<String, String> names = Map.of("einrichtungskontakt", "Facility contact",
                "abteilungskontakt", "Department contact", "versorgungsstellenkontakt", "Care unit contact");
        String level = levels.isEmpty() ? "not specified" : String.join(" + ", levels.stream()
                .sorted().map(value -> names.getOrDefault(value, value)).toList());
        contacts.merge(List.of(level, system, code, hierarchy), 1L, Long::sum);
    }

    private ObjectNode statistics() {
        ObjectNode result = JSON.createObjectNode();
        result.put("bundles", bundles);
        result.set("resources", JSON.valueToTree(occurrences));
        ObjectNode distinct = result.putObject("distinctIds");
        occurrences.keySet().forEach(type -> distinct.put(type, ids.getOrDefault(type, Set.of()).size()));
        result.set("missingIds", JSON.valueToTree(missingByType));
        for (var entry : Map.of("encounters", encounters, "contacts", contacts).entrySet()) {
            var rows = result.putArray(entry.getKey());
            entry.getValue().entrySet().stream().sorted(Comparator.comparing(row -> row.getKey().toString()))
                    .forEach(row -> { var values = rows.addArray(); row.getKey().forEach(values::add); values.add(row.getValue()); });
        }
        return result;
    }
    private final Set<String> formats = new TreeSet<>();

    private void resource(String type, String id) {
        if (type == null || type.equals("Bundle")) return;
        instances++;
        occurrences.merge(type, 1L, Long::sum);
        if (id == null || id.isBlank()) { missingIds++; missingByType.merge(type, 1L, Long::sum); }
        else ids.computeIfAbsent(type, unused -> new HashSet<>()).add(id);
    }

    private void object(JsonParser parser, boolean entries) throws Exception {
        String type = null, id = null;
        String system = "not specified", code = "not specified";
        boolean partOf = false;
        Set<String> levels = new TreeSet<>();
        while (parser.nextToken() != JsonToken.END_OBJECT) {
            if (parser.currentToken() == null) throw new IllegalArgumentException("Truncated JSON object");
            String field = parser.currentName();
            JsonToken token = parser.nextToken();
            if (field.equals("resourceType")) type = parser.getValueAsString();
            else if (field.equals("id")) id = parser.getValueAsString();
            else if (field.equals("class") && token == JsonToken.START_OBJECT) {
                var coding = JSON.<com.fasterxml.jackson.databind.JsonNode>readTree(parser);
                system = coding.path("system").asText("not specified"); code = coding.path("code").asText("not specified");
            } else if (field.equals("partOf") && token == JsonToken.START_OBJECT) {
                partOf = JSON.<com.fasterxml.jackson.databind.JsonNode>readTree(parser).size() > 0;
            } else if (field.equals("type") && token == JsonToken.START_ARRAY) {
                for (var concept : JSON.<com.fasterxml.jackson.databind.JsonNode>readTree(parser)) for (var coding : concept.path("coding"))
                    if (coding.path("system").asText().equals("http://fhir.de/CodeSystem/Kontaktebene"))
                        levels.add(coding.path("code").asText("not specified"));
            } else if (entries && field.equals("entry") && token == JsonToken.START_ARRAY) {
                while (parser.nextToken() != JsonToken.END_ARRAY) {
                    if (parser.currentToken() != JsonToken.START_OBJECT) throw new IllegalArgumentException("Invalid bundle entry");
                    while (parser.nextToken() != JsonToken.END_OBJECT) {
                        String name = parser.currentName();
                        token = parser.nextToken();
                        if (name.equals("resource") && token == JsonToken.START_OBJECT) object(parser, false);
                        else parser.skipChildren();
                    }
                }
            } else parser.skipChildren();
        }
        if (entries && "Bundle".equals(type)) bundles++;
        resource(type, id);
        if ("Encounter".equals(type)) encounter(system, code, partOf, levels);
    }

    private void json(InputStream input) throws Exception {
        try (var parser = JSON.getFactory().createParser(input).disable(JsonParser.Feature.AUTO_CLOSE_SOURCE)) {
            while (parser.nextToken() != null) {
                if (parser.currentToken() != JsonToken.START_OBJECT) throw new IllegalArgumentException("Expected FHIR JSON object");
                object(parser, true);
            }
        }
    }

    private void xml(InputStream input) throws Exception {
        var factory = XMLInputFactory.newFactory();
        factory.setProperty(XMLInputFactory.SUPPORT_DTD, false);
        factory.setProperty("javax.xml.stream.isSupportingExternalEntities", false);
        var parser = factory.createXMLStreamReader(input);
        int depth = 0, resourceDepth = -1;
        boolean bundle = false;
        var elements = new ArrayList<String>();
        String type = null, id = null, system = "not specified", code = "not specified", codingSystem = "", codingCode = "not specified";
        boolean partOf = false;
        Set<String> levels = new TreeSet<>();
        try {
            while (parser.hasNext()) {
                int event = parser.next();
                if (event == XMLStreamConstants.START_ELEMENT) {
                    depth++;
                    elements.add(parser.getLocalName());
                    if (depth == 1) { bundle = parser.getLocalName().equals("Bundle"); if (bundle) bundles++; }
                    if ((!bundle && depth == 1) || (bundle && depth == 4 && elements.get(1).equals("entry") && elements.get(2).equals("resource"))) {
                        resourceDepth = depth; type = parser.getLocalName(); id = null;
                        system = "not specified"; code = "not specified"; partOf = false; levels.clear();
                    } else if (depth == resourceDepth + 1 && parser.getLocalName().equals("id")) {
                        id = parser.getAttributeValue(null, "value");
                    }
                    if (resourceDepth > 0 && "Encounter".equals(type)) {
                        String relative = String.join("/", elements.subList(resourceDepth, elements.size()));
                        String value = Objects.toString(parser.getAttributeValue(null, "value"), "not specified");
                        if (relative.equals("class/system")) system = value;
                        if (relative.equals("class/code")) code = value;
                        if (relative.startsWith("partOf/")) partOf = true;
                        if (relative.equals("type/coding")) { codingSystem = ""; codingCode = "not specified"; }
                        if (relative.equals("type/coding/system")) codingSystem = value;
                        if (relative.equals("type/coding/code")) codingCode = value;
                    }
                } else if (event == XMLStreamConstants.END_ELEMENT) {
                    if (resourceDepth > 0 && "Encounter".equals(type)
                            && String.join("/", elements.subList(resourceDepth, elements.size())).equals("type/coding")
                            && "http://fhir.de/CodeSystem/Kontaktebene".equals(codingSystem)) levels.add(codingCode);
                    if (depth == resourceDepth) {
                        resource(type, id);
                        if ("Encounter".equals(type)) encounter(system, code, partOf, levels);
                        resourceDepth = -1;
                    }
                    elements.remove(elements.size() - 1);
                    depth--;
                }
            }
        } finally { parser.close(); }
    }

    private void file(Path path, String suffix) throws Exception {
        files++;
        formats.add(suffix.substring(1));
        try (var input = Files.newInputStream(path)) {
            if (suffix.equals(".xml")) xml(input);
            else if (suffix.equals(".json.gz")) {
                try (var expanded = new GZIPInputStream(input)) { json(expanded); }
            } else if (suffix.equals(".json.bz2")) {
                try (var expanded = new BZip2CompressorInputStream(input)) { json(expanded); }
            } else if (suffix.equals(".json.zip")) {
                try (var archive = new ZipInputStream(input)) {
                    for (var entry = archive.getNextEntry(); entry != null; entry = archive.getNextEntry())
                        if (!entry.isDirectory() && entry.getName().endsWith(".json")) json(archive);
                }
            } else json(input);
        }
    }

    static List<Path> selectedFiles(Path root) throws Exception {
        List<Path> selectedFiles = new ArrayList<>();
        Map<Path, List<Path>> folders = new TreeMap<>();
        try (var paths = Files.walk(root)) {
            for (Path path : paths.filter(Files::isRegularFile).sorted().toList()) {
                if (Files.isSymbolicLink(path)) throw new IllegalArgumentException("Symbolic links are not dataset files");
                folders.computeIfAbsent(path.getParent(), unused -> new ArrayList<>()).add(path);
            }
        }
        for (var folder : folders.values()) {
            for (String suffix : SUFFIXES) {
                var selected = folder.stream().filter(path -> path.toString().endsWith(suffix)).toList();
                if (selected.isEmpty()) continue;
                selectedFiles.addAll(selected);
                break;
            }
        }
        return selectedFiles;
    }

    public static ObjectNode inspect(Path root) throws Exception {
        var scan = new DatasetInspection();
        for (Path path : selectedFiles(root)) {
            String suffix = SUFFIXES.stream().filter(value -> path.toString().endsWith(value)).findFirst().orElseThrow();
            scan.file(path, suffix);
        }
        var result = JSON.createObjectNode();
        var counts = result.putObject("resourceCounts");
        scan.ids.forEach((type, ids) -> counts.put(type, ids.size()));
        long unique = scan.ids.values().stream().mapToLong(Set::size).sum();
        result.put("patients", scan.ids.getOrDefault("Patient", Set.of()).size());
        result.put("uniqueResources", unique).put("resourceInstances", scan.instances).put("missingIds", scan.missingIds);
        result.put("repeatedIds", scan.instances - scan.missingIds - unique).put("inspectedFiles", scan.files);
        result.set("inspectedFormats", JSON.valueToTree(scan.formats));
        result.set("statistics", scan.statistics());
        return result;
    }

    public static void main(String[] args) throws Exception {
        Files.writeString(Path.of(args[1]), inspect(Path.of(args[0])).toString());
    }
}
