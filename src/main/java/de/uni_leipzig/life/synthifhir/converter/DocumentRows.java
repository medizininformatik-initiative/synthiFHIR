package de.uni_leipzig.life.synthifhir.converter;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Function;

/** Structural rules shared by Excel validation and both CSV import paths. */
public final class DocumentRows {
    public static final String ID = "Dokument-ID";
    public static final List<String> HEADER_FIELDS = List.of("Patient-ID", "Fall-Nr", "Dateipfad", "Embed",
            "Dokumenttext", "Status", "Ausgabezeitpunkt");
    private final Set<String> ids = new HashSet<>();
    private String patient;

    public void reset() { patient = null; }
    public String patient() { return patient; }
    public static boolean present(String value) { return value != null && !value.isBlank(); }

    public List<String> accept(Function<String, String> value) {
        List<String> errors = new ArrayList<>();
        String id = value.apply(ID);
        if (present(id)) {
            reset();
            if (!ids.add(id)) errors.add("Duplicate Dokument-ID: " + id);
            if (!present(value.apply("Patient-ID"))) errors.add("Document start requires Patient-ID");
            boolean embed = "Ja".equalsIgnoreCase(value.apply("Embed"));
            if (!present(value.apply("Dateipfad")) && (!embed || !present(value.apply("Dokumenttext"))))
                errors.add(embed ? "Embed=Ja requires Dokumenttext or Dateipfad" : "Embed empty/Nein requires Dateipfad");
            if (errors.isEmpty()) patient = value.apply("Patient-ID");
        } else {
            if (patient == null) errors.add("Continuation requires a preceding document with Dokument-ID");
            errors.addAll(continuationErrors(value));
        }
        return errors;
    }

    public static List<String> continuationErrors(Function<String, String> value) {
        return HEADER_FIELDS.stream().filter(f -> present(value.apply(f)))
                .map(f -> f + " belongs on the document start row with Dokument-ID").toList();
    }
}
