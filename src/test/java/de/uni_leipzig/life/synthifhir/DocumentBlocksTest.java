package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.nio.file.*;
import java.util.*;
import org.apache.commons.csv.*;
import org.hl7.fhir.r4.model.*;
import org.junit.*;
import org.junit.rules.TemporaryFolder;
import de.uni_leipzig.life.synthifhir.converter.DocumentReferenceConverter;
import de.uni_leipzig.life.synthifhir.converter.DocumentRows;

public class DocumentBlocksTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();
    private ImportReport report;
    private String documentSettings = "";
    private List<DocumentReference> run(List<Map<String, String>> rows) throws Exception {
        Path input = temp.newFolder().toPath(), output = temp.newFolder().toPath();
        Files.writeString(input.resolve("case_Person.csv"),
                "Patient-ID,Vorname,Nachname,Geburtsdatum,Geschlecht\np1,Test,Person,2000-01-01,weiblich\np2,Test,Other,2001-01-01,weiblich\n");
        Set<String> contactColumns = new LinkedHashSet<>(TableIdentifier.Fall.getMandatoryColumnNames());
        var contact = Map.of("Patient-ID", "p1", "Fall-Nr", "9", "Start", "2026-01-01", "Ende", "2026-12-31", "Einrichtungskontaktklasse", "stationaer");
        contactColumns.addAll(contact.keySet());
        try (var writer = Files.newBufferedWriter(input.resolve("case_Fall.csv"));
                var csv = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
            csv.printRecord(contactColumns);
            csv.printRecord(contactColumns.stream().map(c -> contact.getOrDefault(c, "")).toList());
        }
        Set<String> columns = new LinkedHashSet<>(TableIdentifier.DocumentReference.getMandatoryColumnNames());
        columns.addAll(List.of("Patient-ID", "Fall-Nr", "Dokumenttext", "NLPStatus", "Dokumentcode", "Dokumentcodesystem", "Dokumentbezeichner"));
        try (var writer = Files.newBufferedWriter(input.resolve("case_DocumentReference.csv"));
                var csv = new CSVPrinter(writer, CSVFormat.DEFAULT)) {
            csv.printRecord(columns);
            for (var row : rows) csv.printRecord(columns.stream().map(c -> row.getOrDefault(c, "")).toList());
        }
        var options = ConverterOptions.fromText("CONFIGURATION_VERSION=1\nOUTPUT_PATIENTS_PER_FILE=1000\nREFERENCE_DOCUMENT_REFERENCE_ASSIGNMENT_STRATEGY=explicit-only\n" + documentSettings);
        var converter = new Csv2Fhir(input.toFile(), output.toFile(), "case_", null, options);
        converter.convertFiles(Integer.MAX_VALUE, OutputFileType.JSON);
        report = converter.getImportReport();
        var documents = new ArrayList<DocumentReference>();
        if (!Files.exists(output.resolve("case.json"))) return documents;
        var bundle = (Bundle)OutputFileType.JSON.getParser().parseResource(Files.readString(output.resolve("case.json")));
        for (var entry : bundle.getEntry())
            if (entry.getResource() instanceof DocumentReference) documents.add((DocumentReference)entry.getResource());
        return documents;
    }
    private Map<String, String> start(String id, String patient, String text) {
        return new LinkedHashMap<>(Map.of("Dokument-ID", id, "Patient-ID", patient, "Dokumenttext", text, "Embed", "Ja"));
    }
    private List<String> codes(DocumentReference doc) {
        return ((CodeableConcept)doc.getExtensionByUrl(DocumentReferenceConverter.NLP_EXTENSION).getValue())
                .getCoding().stream().map(Coding::getCode).toList();
    }
    @Test public void collectsIndependentListsAndPreservesTheStartRowContext() throws Exception {
        var first = start("d1", "p1", "Ärztlicher Text");
        first.putAll(Map.of("NLPStatus", "preprocessed", "Dokumentcode", "AD010110",
                "Dokumentcodesystem", "http://dvmd.de/fhir/CodeSystem/kdl", "Dokumentbezeichner", "Ärztlicher Verlaufsbericht",
                "Fall-Nr", "9"));
        var docs = run(List.of(first, Map.of("NLPStatus", "format-change", "Dokumentcode", "BERI",
                "Dokumentcodesystem", "http://ihe-d.de/CodeSystems/IHEXDStypeCode", "Dokumentbezeichner", "Arztberichte"),
                Map.of("NLPStatus", "surrogated"), start("d2", "p2", "Other")));
        assertFalse(report.hasErrors());
        assertEquals(2, docs.size());
        var doc = docs.stream().filter(d -> d.getSubject().getReference().equals("Patient/p1")).findFirst().orElseThrow();
        assertEquals(2, doc.getType().getCoding().size());
        assertEquals("Arztberichte", doc.getType().getCoding().get(1).getDisplay());
        assertEquals(List.of("preprocessed", "format-change", "surrogated"), codes(doc));
        assertEquals(1, doc.getExtension().size());
        for (Coding code : ((CodeableConcept)doc.getExtension().get(0).getValue()).getCoding())
            assertEquals(DocumentReferenceConverter.NLP_SYSTEM, code.getSystem());
        assertTrue(doc.getContext().getEncounterFirstRep().getReference().endsWith("9"));
        assertEquals("Ärztlicher Text", new String(doc.getContentFirstRep().getAttachment().getData(), java.nio.charset.StandardCharsets.UTF_8));
        assertTrue(report.documentIssues.isEmpty());
        assertEquals(4, report.tables.get("DocumentReference").processedRows);
    }
    @Test public void settingsApplyAfterContinuationRowsAndReportConfiguredUnknownCodes() throws Exception {
        documentSettings = "DOCUMENT_TYPE_MODE=fill\nDOCUMENT_TYPE_VALUES=[{\"code\":\"fallback\"}]\n"
                + "DOCUMENT_NLP_MODE=fill\nDOCUMENT_NLP_VALUES=[\"custom\"]\n";
        var docs = run(List.of(start("d1", "p1", "Text"),
                Map.of("Dokumentcode", "source", "NLPStatus", "unprocessed"), start("d2", "p2", "Other")));
        var first = docs.stream().filter(d -> d.getSubject().getReference().equals("Patient/p1")).findFirst().orElseThrow();
        var second = docs.stream().filter(d -> d.getSubject().getReference().equals("Patient/p2")).findFirst().orElseThrow();
        assertEquals("source", first.getType().getCodingFirstRep().getCode());
        assertEquals(List.of("unprocessed"), codes(first));
        assertEquals("fallback", second.getType().getCodingFirstRep().getCode());
        assertEquals(List.of("custom"), codes(second));
        assertFalse(report.documentIssues.isEmpty());
        assertFalse(report.hasErrors());
    }
    @Test public void embedsOnlyOnJaAndOtherwiseDoesNotReadTheFile() throws Exception {
        Path file = temp.newFile("source.txt").toPath(); Files.writeString(file, "Datei ä");
        var text = start("text", "p1", "Zelle"); text.put("Dateipfad", "\u0000ignored-path");
        var embedded = start("file", "p1", ""); embedded.put("Dateipfad", file.toString());
        var url = start("url", "p1", "Nicht ausgeben"); url.put("Embed", ""); url.put("Dateipfad", file.toString());
        var no = new LinkedHashMap<>(url); no.put("Dokument-ID", "no"); no.put("Embed", "Nein");
        var docs = run(List.of(text, embedded, url, no));
        assertFalse(report.hasErrors()); assertEquals(4, docs.size());
        assertEquals("Zelle", new String(docs.get(0).getContentFirstRep().getAttachment().getData(), java.nio.charset.StandardCharsets.UTF_8));
        assertEquals("Datei ä", new String(docs.get(1).getContentFirstRep().getAttachment().getData(), java.nio.charset.StandardCharsets.UTF_8));
        for (var doc : docs.subList(2, 4)) {
            var attachment = doc.getContentFirstRep().getAttachment();
            assertTrue(attachment.hasUrl()); assertFalse(attachment.hasData());
            assertFalse(attachment.hasSize()); assertFalse(attachment.hasCreation());
        }
    }
    @Test public void reportsUnknownCodesWithoutRepairOrLoss() throws Exception {
        var docs = run(List.of(start("d1", "p1", "Text"), Map.of("NLPStatus", "deid"), Map.of("NLPStatus", "invalid-code")));
        assertFalse(report.hasErrors()); assertEquals(1, docs.size());
        assertEquals(List.of("deid", "invalid-code"), codes(docs.get(0)));
        assertEquals(2, report.documentIssues.size());
    }
    @Test public void rejectedStartsCannotAttachTheirContinuationsToThePreviousDocument() throws Exception {
        var docs = run(List.of(start("d1", "p1", "Text"), Map.of("Dokument-ID", "broken", "Patient-ID", "p1"),
                Map.of("NLPStatus", "surrogated"), start("d2", "p1", "Next")));
        assertTrue(report.hasErrors()); assertEquals(0, docs.size());
        assertTrue(docs.stream().noneMatch(d -> d.hasExtension(DocumentReferenceConverter.NLP_EXTENSION)));
        assertEquals(2, report.tables.get("DocumentReference").failedRows);
    }
    @Test public void failedConversionDoesNotReuseThePreviousDocument() throws Exception {
        var broken = start("broken", "p1", ""); broken.put("Dateipfad", "\u0000invalid-path");
        var docs = run(List.of(start("d1", "p1", "First"), broken, Map.of("NLPStatus", "surrogated"), start("d2", "p1", "Next")));
        assertTrue(report.hasErrors()); assertEquals(2, docs.size());
        assertTrue(docs.stream().noneMatch(d -> d.hasExtension(DocumentReferenceConverter.NLP_EXTENSION)));
        assertEquals(2, report.tables.get("DocumentReference").failedRows);
    }
    @Test public void structuralRulesRejectOrphansDuplicateIdsAndHeaderValuesOnContinuation() {
        var state = new DocumentRows();
        assertFalse(state.accept(Map.of("NLPStatus", "unprocessed")::get).isEmpty());
        assertTrue(state.accept(start("d1", "p1", "Text")::get).isEmpty());
        assertFalse(state.accept(Map.of("Fall-Nr", "2")::get).isEmpty());
        assertFalse(state.accept(start("d1", "p1", "Text")::get).isEmpty());
        assertNull(state.patient());
    }
}
