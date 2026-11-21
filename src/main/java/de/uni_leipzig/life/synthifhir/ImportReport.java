package de.uni_leipzig.life.synthifhir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import com.google.gson.GsonBuilder;

/** Input accounting, independent of FHIR validation and Synthea projection losses. */
public final class ImportReport {
    public final int schemaVersion = 1;
    public String status = "COMPLETE";
    public final Map<String, Table> tables = new LinkedHashMap<>();
    public final List<Issue> issues = new ArrayList<>();
    public final List<Map<String, String>> documentIssues = new ArrayList<>();
    public final List<Map<String, String>> timeShifts = new ArrayList<>();
    public final List<Map<String, String>> contactEndDerivations = new ArrayList<>();
    public final List<Map<String, String>> outputSelections = new ArrayList<>();
    public final List<Map<String, String>> diagnosisReferenceIssues = new ArrayList<>();
    public final List<Map<String, String>> encounterReferenceIssues = new ArrayList<>();
    public final List<Map<String, String>> medicationTransformations = new ArrayList<>();

    public static final class Table {
        public String file;
        public long rowsRead, emptyRows, processedRows, failedRows, unprocessedRows, successfulAttempts, failedAttempts, returnedResources;
        public boolean rejected;
        private transient Set<Long> processed = new HashSet<>(), failed = new HashSet<>();
    }
    public static final class Issue {
        public String table, category, reason, outputPrefix;
        public Long record;
        public Integer iteration;
        Issue(String table, Long record, String category, String reason, ConverterOptions options) {
            this.table = table; this.record = record; this.category = category; this.reason = reason;
            if (options != null) { outputPrefix = options.getPrefixWithSuffix(); iteration = options.loopCounter; }
        }
    }
    public Table table(TableIdentifier id, String file) {
        return tables.computeIfAbsent(id.name(), k -> { Table t = new Table(); t.file = file; return t; });
    }
    public void failure(TableIdentifier id, Long record, String category, String reason, ConverterOptions options) {
        status = "INCOMPLETE";
        // Converter exceptions historically append complete CSV records. Keep the cause, not a second data dump.
        String concise = Objects.toString(reason, "Unknown error").split("CSVRecord", 2)[0].strip();
        issues.add(new Issue(id == null ? null : id.name(), record, category, concise, options));
        if (id != null && record == null) tables.get(id.name()).rejected = true;
        if (id != null && record != null) {
            Table t = tables.get(id.name());
            t.failed.add(record); t.failedRows = t.failed.size();
            if (options != null) t.failedAttempts++;
        }
    }
    public void success(TableIdentifier id, long record, int resources) {
        Table t = tables.get(id.name());
        t.processed.add(record); t.processedRows = t.processed.size();
        t.successfulAttempts++; t.returnedResources += resources;
    }
    public static String describe(Exception error) {
        Throwable cause = error;
        while (cause instanceof java.lang.reflect.InvocationTargetException && cause.getCause() != null) cause = cause.getCause();
        return cause.getClass().getSimpleName() + ": " + Objects.toString(cause.getMessage(), "no further error details");
    }
    public boolean hasErrors() { return !issues.isEmpty(); }
    public void write(Path path) throws IOException {
        for (Table t : tables.values()) {
            Set<Long> accounted = new HashSet<>(t.processed); accounted.addAll(t.failed);
            t.unprocessedRows = Math.max(0, t.rowsRead - t.emptyRows - accounted.size());
        }
        Files.createDirectories(path.toAbsolutePath().getParent());
        try (var writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
            new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create().toJson(this, writer);
        }
    }
}
