package de.uni_leipzig.imise;

import java.io.InputStreamReader;
import java.io.PrintStream;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import com.google.gson.JsonParser;
import com.google.gson.JsonObject;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.core.LoggerContext;
import de.uni_leipzig.UcumMapper;
import de.uni_leipzig.life.synthifhir.PrintExceptionMessageHandler;
import picocli.CommandLine;

/** Internal sequential worker for one Synthea import; job metadata arrives on stdin. */
public final class Excel2FhirBatch {
    public static void main(String[] args) throws Exception {
        var jobs = JsonParser.parseReader(new InputStreamReader(System.in, StandardCharsets.UTF_8)).getAsJsonArray();
        if (jobs.isEmpty()) return;
        var first = jobs.get(0).getAsJsonObject();
        if (!first.has("identifierCountersOutput")) {
            for (var element : jobs) execute(element.getAsJsonObject());
            return;
        }
        var json = new com.google.gson.Gson();
        var counters = new java.util.HashMap<String, Long>();
        first.getAsJsonObject("identifierCounters").entrySet()
                .forEach(entry -> counters.put(entry.getKey(), entry.getValue().getAsLong()));
        try (var journal = Files.newBufferedWriter(Path.of(first.get("identifierRegistrationsOutput").getAsString()))) {
            var sequence = new IdentifierSequence(counters, registration -> {
                try { journal.write(json.toJson(registration)); journal.newLine(); }
                catch (java.io.IOException error) { throw new java.io.UncheckedIOException(error); }
            });
            for (var element : jobs) execute(element.getAsJsonObject(), sequence);
            Files.writeString(Path.of(first.get("identifierCountersOutput").getAsString()), json.toJson(sequence.counters()));
        }
    }

    static void execute(JsonObject job) throws Exception { execute(job, null); }

    static void execute(JsonObject job, IdentifierSequence sequence) throws Exception {
        PrintStream previousOut = System.out, previousErr = System.err;
        long started = System.nanoTime();
        int exitCode;
        try (var log = new PrintStream(Files.newOutputStream(Path.of(job.get("log").getAsString())),
                true, StandardCharsets.UTF_8);
                var writer = new PrintWriter(log, true)) {
            System.setOut(log);
            System.setErr(log);
            // Console appenders capture their stream when configured; bind them to this case.
            ((LoggerContext) LogManager.getContext(false)).reconfigure();
            UcumMapper.invalidUcumCodes.clear();
            String[] arguments = new com.google.gson.Gson().fromJson(job.get("arguments"), String[].class);
            var converter = new Excel2FhirMain(job.has("csvDirectory") ? new java.io.File(job.get("csvDirectory").getAsString()) : null);
            converter.identifierSequence = sequence;
            exitCode = new CommandLine(converter)
                    .setOut(writer).setErr(writer)
                    .setExecutionExceptionHandler(new PrintExceptionMessageHandler()).execute(arguments);
        } finally {
            UcumMapper.invalidUcumCodes.clear();
            System.setOut(previousOut);
            System.setErr(previousErr);
            ((LoggerContext) LogManager.getContext(false)).reconfigure();
        }
        var result = new JsonObject();
        result.addProperty("exitCode", exitCode);
        result.addProperty("seconds", (System.nanoTime() - started) / 1e9);
        Files.writeString(Path.of(job.get("result").getAsString()), result.toString());
    }
}
