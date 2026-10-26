package de.uni_leipzig.life.synthifhir;

import java.io.IOException;
import java.io.StringReader;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.BooleanNode;
import com.fasterxml.jackson.databind.node.LongNode;
import com.fasterxml.jackson.databind.node.TextNode;

/** Stored selections and effective dependencies from the shared versioned contract. */
public final class ContractConfiguration {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final JsonNode CONTRACT = catalogue("options/contract.json");
    private static final JsonNode DAR = catalogue("dar/generated/catalog.json");
    private static final Pattern ASSIGNMENT = Pattern.compile("([A-Z][A-Z0-9_]*)\\s*=\\s*(.*)");
    private static final Pattern RULE = Pattern.compile("IDENTIFIER_RULE_([1-9][0-9]*)_(ID|ENABLED|RESOURCES|SYSTEM|PATTERN|SCOPE|COUNT_START|USE|TYPE_TEXT|TYPE_CODINGS)");
    private final Map<String, JsonNode> definitions = new LinkedHashMap<>();
    private final Map<String, JsonNode> values = new LinkedHashMap<>();
    private final Map<String, String> dar = new LinkedHashMap<>();
    private final Set<String> darMissingOnly = new HashSet<>();
    private final List<Map<String, String>> rules = new ArrayList<>();

    private static JsonNode catalogue(String name) {
        try (var stream = ContractConfiguration.class.getResourceAsStream("/configuration/" + name)) {
            if (stream == null) throw new IllegalStateException("Missing configuration catalogue: " + name);
            return JSON.readTree(stream);
        } catch (IOException e) { throw new IllegalStateException("Cannot load configuration catalogue", e); }
    }

    private ContractConfiguration() {
        for (JsonNode option : CONTRACT.get("options")) {
            String id = option.get("id").asText();
            definitions.put(id, option);
            values.put(id, option.get("default").deepCopy());
        }
    }

    /** Export the same defaults used by the editor and command-line conversion. */
    public static String defaultProperties() {
        Properties properties = new Properties();
        properties.setProperty("CONFIGURATION_VERSION", "1");
        for (JsonNode option : CONTRACT.get("options")) {
            JsonNode value = option.get("default");
            var parts = new ArrayList<String>();
            if (value.isArray()) value.forEach(item -> parts.add(item.asText()));
            properties.setProperty(option.get("propertyName").asText(),
                    value.isArray() ? String.join(",", parts) : value.asText());
        }
        var writer = new java.io.StringWriter();
        try { properties.store(writer, "Converter configuration"); }
        catch (IOException error) { throw new IllegalStateException(error); }
        return writer.toString();
    }

    /** Also detects malformed versioned files so they cannot fall through to legacy options. */
    public static boolean isContractText(String text) {
        Set<String> names = new HashSet<>();
        for (JsonNode option : CONTRACT.get("options")) {
            if (!option.path("binding").path("name").asText().equals(option.get("propertyName").asText()))
                names.add(option.get("propertyName").asText());
        }
        for (String line : text.replace("\uFEFF", "").split("\\R")) {
            String key = line.stripLeading().replaceFirst("^#\\s*", "").split("[\\s=:]", 2)[0];
            if (key.equals("CONFIGURATION_VERSION") || key.startsWith("DAR_")
                    || key.startsWith("IDENTIFIER_RULE_") || names.contains(key)) return true;
        }
        return false;
    }

    public static ContractConfiguration parse(String text) {
        ContractConfiguration config = new ContractConfiguration();
        Map<String, String> names = new LinkedHashMap<>();
        config.definitions.forEach((id, option) -> names.put(option.get("propertyName").asText(), id));
        Map<String, String> darNames = new LinkedHashMap<>();
        CONTRACT.path("propertiesFormat").path("darProperties").properties()
                .forEach(e -> darNames.put(e.getValue().asText(), e.getKey()));
        Map<Integer, Map<String, String>> parsedRules = new TreeMap<>();
        Set<String> seen = new HashSet<>();
        boolean version = false;
        for (String raw : text.replaceFirst("^\uFEFF", "").split("\\r?\\n", -1)) {
            String line = raw.stripLeading();
            if (line.isEmpty() || line.startsWith("!")) continue;
            boolean commented = line.startsWith("#");
            if (commented) line = line.substring(1).stripLeading();
            var match = ASSIGNMENT.matcher(line);
            if (!match.matches()) {
                if (commented) continue;
                throw invalid("Expected NAME = VALUE: " + line);
            }
            String name = match.group(1);
            if (!seen.add(name)) throw invalid("Duplicate property: " + name);
            String value = unescape(match.group(2), name);
            if (name.equals("CONFIGURATION_VERSION")) {
                if (commented || !value.equals("1")) throw invalid("Unsupported configuration version");
                version = true;
            } else if (Set.of("PID_LAST_NUMBER_INCREASE_INITIAL_OFFSET", "PID_LAST_NUMBER_INCREASE_LOOP_OFFSET").contains(name)) {
                if (!value.equals("0")) throw invalid("Use patient ID prefixes or suffixes instead of numeric offsets");
            } else if (name.equals("ADD_MISSING_DIAGNOSES_FROM_SUPER_ENCOUNTER")) {
                booleanValue(value); // Retired editor setting; assignment is automatic.
            } else if (names.containsKey(name)) {
                String id = names.get(name);
                config.values.put(id, parseValue(value, config.definitions.get(id)));
            } else if (name.endsWith("_ONLY_WHEN_MISSING") && darNames.containsKey(name.substring(0, name.length() - 18))) {
                if (booleanValue(value)) config.darMissingOnly.add(darNames.get(name.substring(0, name.length() - 18)));
            } else if (darNames.containsKey(name)) {
                String id = darNames.get(name);
                JsonNode field = null;
                String source = id;
                for (JsonNode scope : CONTRACT.path("dar").path("scopedFields"))
                    if (scope.path("id").asText().equals(id)) source = scope.path("source").asText();
                for (JsonNode candidate : DAR.get("fields")) if (candidate.get("id").asText().equals(source)) field = candidate;
                if (!value.equals("unchanged")) {
                    if (value.startsWith("!dar:")) value = value.substring(5);
                    if (field == null || value.isBlank())
                        throw invalid("Invalid DAR code: " + name);
                    config.dar.put(id, value);
                }
            } else {
                var rule = RULE.matcher(name);
                if (!rule.matches()) throw invalid("Unknown property: " + name);
                int index;
                try { index = Integer.parseInt(rule.group(1)); }
                catch (NumberFormatException e) { throw invalid("Invalid rule index: " + name); }
                parsedRules.computeIfAbsent(index, k -> new LinkedHashMap<>()).put(rule.group(2), value);
            }
        }
        if (!version) throw invalid("Missing CONFIGURATION_VERSION");
        Set<String> ruleIds = new HashSet<>();
        for (var entry : parsedRules.entrySet()) {
            Map<String, String> rule = entry.getValue();
            if (entry.getKey() != config.rules.size() + 1 || !rule.keySet().containsAll(Set.of("ID", "ENABLED", "RESOURCES", "SYSTEM", "PATTERN")))
                throw invalid("Incomplete identifier rule: " + entry.getKey());
            String id = rule.get("ID").toLowerCase(Locale.ROOT);
            if (!id.matches("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}") || !ruleIds.add(id))
                throw invalid("Invalid or duplicate rule ID: " + id);
            booleanValue(rule.get("ENABLED"));
            Set<String> types = new HashSet<>();
            for (String resource : rule.get("RESOURCES").split(",", -1)) {
                boolean eligible = false;
                for (JsonNode candidate : CONTRACT.get("resources")) {
                    if (candidate.path("identifierEligible").asBoolean() && candidate.path("identifierSelector").asText(candidate.get("resourceType").asText()).equals(resource)) eligible = true;
                }
                for (JsonNode scope : CONTRACT.path("identifierScopes"))
                    if (scope.path("selector").asText().equals(resource)) eligible = true;
                if (!eligible || !types.add(resource)) throw invalid("Invalid identifier resource: " + resource);
            }
            if (rule.get("SYSTEM").isEmpty() || rule.get("PATTERN").isEmpty()) throw invalid("Empty identifier system or pattern");
            String scope = rule.getOrDefault("SCOPE", "resource");
            if (!Set.of("resource", "case").contains(scope)) throw invalid("Invalid identifier scope: " + scope);
            validatePattern(rule.get("PATTERN"), scope.equals("case"));
            String start = rule.getOrDefault("COUNT_START", "1");
            try {
                if (!start.matches("[0-9]+") || Long.parseLong(start) < 1 || Long.parseLong(start) > 9007199254740991L)
                    throw invalid("Invalid identifier counter start: " + start);
            } catch (NumberFormatException e) { throw invalid("Invalid identifier counter start: " + start); }
            if (rule.containsKey("USE") && !Set.of("", "usual", "official", "temp", "secondary", "old").contains(rule.get("USE")))
                throw invalid("Invalid Identifier.use: " + rule.get("USE"));
            identifierTypeCodings(rule);
            config.rules.add(Map.copyOf(rule));
        }
        if (config.stored("output.formats").isEmpty()) throw invalid("Select at least one output format");
        if (!config.stored("output.patientsPerFile").canConvertToInt())
            throw invalid("OUTPUT_PATIENTS_PER_FILE exceeds the Java integer range");
        return config;
    }

    static JsonNode identifierTypeCodings(Map<String, String> rule) {
        try {
            JsonNode codings = JSON.readTree(rule.getOrDefault("TYPE_CODINGS", "[]"));
            if (codings == null || !codings.isArray()) throw invalid("Identifier TYPE_CODINGS must be a JSON array");
            for (JsonNode coding : codings) {
                if (!coding.isObject()) throw invalid("Identifier coding must be an object");
                for (var field : coding.properties()) {
                    if (!Set.of("system", "code", "display").contains(field.getKey()) || !field.getValue().isTextual())
                        throw invalid("Invalid identifier coding field: " + field.getKey());
                }
            }
            return codings;
        } catch (IOException error) { throw invalid("Invalid identifier TYPE_CODINGS JSON"); }
    }

    private static String unescape(String value, String name) {
        for (int i = 0; i < value.length(); i++) {
            if (value.charAt(i) != '\\') continue;
            if (++i == value.length()) throw invalid("Continuation not allowed: " + name);
            if (value.charAt(i) == 'u') {
                if (i + 4 >= value.length() || !value.substring(i + 1, i + 5).matches("[0-9a-fA-F]{4}"))
                    throw invalid("Invalid Unicode escape: " + name);
                i += 4;
            }
        }
        Properties decoded = new Properties();
        try { decoded.load(new StringReader("value=" + value)); }
        catch (IOException e) { throw new IllegalStateException(e); }
        return decoded.getProperty("value");
    }

    private static JsonNode parseValue(String text, JsonNode option) {
        String type = option.get("type").asText();
        JsonNode value;
        if (type.equals("boolean")) value = BooleanNode.valueOf(booleanValue(text));
        else if (type.equals("integer") || option.get("default").isNumber()) {
            long number;
            try { number = Long.parseLong(text); }
            catch (NumberFormatException e) { throw invalid("Invalid integer: " + option.get("propertyName").asText()); }
            if (!text.matches("-?[0-9]+") || Math.abs((double)number) > 9007199254740991L
                    || (option.has("minimum") && number < option.get("minimum").asLong()))
                throw invalid("Integer outside range: " + option.get("propertyName").asText());
            value = LongNode.valueOf(number);
        } else if (type.equals("set")) {
            ArrayNode array = JSON.createArrayNode();
            Set<String> seen = new HashSet<>();
            if (!text.isEmpty()) for (String part : text.split(",", -1)) {
                if (!seen.add(part) || !contains(option.get("choices"), TextNode.valueOf(part)))
                    throw invalid("Invalid set value: " + option.get("propertyName").asText());
                array.add(part);
            }
            value = array;
        } else value = TextNode.valueOf(text);
        if (type.equals("enum") && !contains(option.get("choices"), value))
            throw invalid("Invalid choice: " + option.get("propertyName").asText());
        return value;
    }

    private static boolean equalValue(JsonNode a, JsonNode b) {
        return a.isNumber() && b.isNumber() ? a.decimalValue().compareTo(b.decimalValue()) == 0 : a.equals(b);
    }
    private static boolean contains(JsonNode array, JsonNode value) {
        for (JsonNode item : array) if (equalValue(item, value)) return true;
        return false;
    }
    private static boolean booleanValue(String value) {
        if (!value.equals("true") && !value.equals("false")) throw invalid("Expected true or false");
        return value.equals("true");
    }
    private static IllegalArgumentException invalid(String reason) { return new IllegalArgumentException(reason); }

    private static void validatePattern(String pattern, boolean caseScoped) {
        for (int i = 0; i < pattern.length();) {
            if (pattern.startsWith("{{", i) || pattern.startsWith("}}", i)) { i += 2; continue; }
            char c = pattern.charAt(i++);
            if (c == '}') throw invalid("Unmatched identifier brace");
            if (c != '{') continue;
            int end = pattern.indexOf('}', i);
            if (end < 0) throw invalid("Unmatched identifier brace");
            String token = pattern.substring(i, end);
            if (caseScoped) {
                if (!Set.of("patientId", "caseId").contains(token)) throw invalid("Invalid case identifier token: " + token);
                i = end + 1;
                continue;
            }
            if (!Set.of("count", "patientId", "resourceId", "resourceType", "iteration", "hash").contains(token)) {
                if (!token.matches("count:0[1-9][0-9]*")) throw invalid("Unknown identifier token: " + token);
                try {
                    if (Long.parseLong(token.substring(7)) > 9007199254740991L) throw invalid("Identifier padding outside range");
                } catch (NumberFormatException e) { throw invalid("Identifier padding outside range"); }
            }
            i = end + 1;
        }
    }

    List<Map<String, String>> identifierRules() { return List.copyOf(rules); }
    /** Immutable DAR selections for independent workflow checks. */
    public Set<String> darMissingOnly() { return Set.copyOf(darMissingOnly); }
    public Map<String, String> darOverrides() { return Map.copyOf(dar); }
    JsonNode darFields() { return DAR.get("fields").deepCopy(); }

    public JsonNode stored(String id) { return values.get(id).deepCopy(); }

    private boolean enabled(String id, Set<String> visiting) {
        if (!visiting.add(id)) throw new IllegalStateException("Cyclic configuration dependency: " + id);
        boolean enabled = dependencies(definitions.get(id).path("enabledWhen"), visiting);
        if (id.endsWith(".endApplication") && values.get(id.replace(".endApplication", ".endPolicy")).asText().equals("preserve")) enabled = false;
        visiting.remove(id);
        return enabled;
    }
    private boolean dependencies(JsonNode dependencies, Set<String> visiting) {
        for (JsonNode dependency : dependencies) {
            String id = dependency.get("option").asText();
            if (!equalValue(values.get(id), dependency.get("equals")) || !enabled(id, visiting)) return false;
        }
        return true;
    }
    /** Empty means ineffective; it must never be replaced with another choice's default. */
    public Optional<JsonNode> effective(String id) {
        JsonNode value = values.get(id);
        if (!enabled(id, new HashSet<>()) || !dependencies(definitions.get(id).path("choiceDependencies").path(value.asText()), new HashSet<>()))
            return Optional.empty();
        return Optional.of(value.deepCopy());
    }

    /** Exact legacy semantics map to properties; contact assignment uses output projections. */
    Map<String, String> javaProperties() {
        Map<String, String> result = new LinkedHashMap<>();
        definitions.forEach((id, option) -> {
            if (directBinding(id)) effective(id).ifPresent(value -> result.put(option.path("binding").get("name").asText(), value.asText()));
        });
        String version = stored("terminology.versionOutput").asText();
        result.put("SYNTHEA_VERSION_OUTPUT", version.equals("catalogue-year") ? "Jahr" : "");
        return result;
    }
    private boolean directBinding(String id) {
        JsonNode binding = definitions.get(id).path("binding");
        return binding.path("kind").asText().equals("java-property") && !binding.has("values") && !binding.has("semantics");
    }

    public List<String> unsupportedSettings() {
        List<String> errors = new ArrayList<>();
        definitions.forEach((id, option) -> {
            if (id.startsWith("resource.Encounter.ambulatory.") || id.startsWith("resource.Encounter.inpatient.")) {
                return;
            }
            if (!Set.of("resource.Patient.mode", "resource.Condition.enabled", "contact.diagnoses.enabled",
                    "resource.Encounter.stationServiceProvider", "resource.Encounter.stationLocation",
                    "contact.diagnoses.levels", "contact.diagnoses.roles", "resource.Encounter.enabled",
                    "contact.facility.enabled", "contact.department.enabled", "contact.ward-service.enabled",
                    "contact.department.partOf", "contact.ward-service.partOf",
                    "contact.procedureDiagnoses.enabled", "contact.procedureDiagnoses.levels",
                    "terminology.versionOutput", "checks.fhirValidation", "output.formats", "output.patientsPerFile").contains(id)
                    && !ClinicalTimeShift.supports(id)
                    && !ClinicalEncounterAssignment.supports(id)
                    && !SupportResourceIdentity.supports(id)
                    && !ResourceOutputPolicy.supports(id)
                    && !ObservationOutputPolicy.supports(id)
                    && !MedicationTransformations.supports(id)
                    && !directBinding(id) && effective(id).isPresent())
                errors.add("Not implemented for configuration version 1: " + option.get("propertyName").asText() + " = " + values.get(id));
        });
        return List.copyOf(errors);
    }
}
