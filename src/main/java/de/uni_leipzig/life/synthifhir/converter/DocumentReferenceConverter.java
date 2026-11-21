package de.uni_leipzig.life.synthifhir.converter;

import static de.uni_leipzig.life.synthifhir.ConverterOptions.IntOption.START_ID_DOCUMENT_REFERENCE;
import static de.uni_leipzig.life.synthifhir.TableIdentifier.DocumentReference;
import static de.uni_leipzig.life.synthifhir.converter.DocumentReferenceConverter.DocumentReference_Columns.Dateipfad;
import static de.uni_leipzig.life.synthifhir.converter.DocumentReferenceConverter.DocumentReference_Columns.Embed;
import static java.util.Collections.singletonList;
import static org.apache.logging.log4j.util.Strings.isBlank;

import java.io.File;
import java.io.IOException;
import java.net.URLConnection;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.FileTime;
import java.util.Date;
import java.util.List;

import org.apache.commons.csv.CSVRecord;
import org.hl7.fhir.r4.model.Attachment;
import org.hl7.fhir.r4.model.CodeableConcept;
import org.hl7.fhir.r4.model.DocumentReference;
import org.hl7.fhir.r4.model.DocumentReference.DocumentReferenceContentComponent;
import org.hl7.fhir.r4.model.DocumentReference.DocumentReferenceContextComponent;
import org.hl7.fhir.r4.model.Resource;

import de.uni_leipzig.imise.validate.FHIRValidator;
import de.uni_leipzig.life.synthifhir.Converter;
import de.uni_leipzig.life.synthifhir.ConverterOptions;
import de.uni_leipzig.life.synthifhir.ConverterResult;
import de.uni_leipzig.life.synthifhir.TableColumnIdentifier;

/**
 * @author FAM (24.07.2023), AXS (06.08.23)
 */
public class DocumentReferenceConverter extends Converter {

    /**
     * toString() result of these enum values are the names of the columns in the
     * correspunding excel sheet.
     */
    public static enum DocumentReference_Columns implements TableColumnIdentifier {
        Dokument_ID {
            @Override public String toString() { return DocumentRows.ID; }
        },
        NLPStatus {
            @Override public boolean isMandatory() { return false; }
        },
        Dateipfad,
        Embed,
    }

    public static final String NLP_EXTENSION = "https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/StructureDefinition/mii-ex-dokument-nlp-processing-status";
    public static final String NLP_SYSTEM = "https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/CodeSystem/mii-cs-dokument-nlp-processing-status";
    public static final java.util.Set<String> NLP_CODES = java.util.Set.of("unprocessed", "preprocessed", "format-change",
            "content-change", "annotated", "preanno", "deid", "semantic", "surrogated");

    private String value(String column) { return hasColumn(column) ? get(column) : null; }

    private void appendCodings(DocumentReference document) {
        CodeableConcept type = ClinicalValues.concept(ClinicalValues.get(this, ClinicalValues.Column.Dokumentcode),
                ClinicalValues.get(this, ClinicalValues.Column.Dokumentcodesystem),
                ClinicalValues.get(this, ClinicalValues.Column.Dokumentbezeichner));
        if (!type.isEmpty()) {
            if (type.hasCoding()) {
                if (type.hasText()) type.getCodingFirstRep().setDisplay(type.getText());
                type.getCoding().forEach(document.getType()::addCoding);
            } else if (type.hasText()) {
                String previous = document.getType().getText();
                document.getType().setText(previous == null ? type.getText() : previous + "\n" + type.getText());
            }
        }
        String status = value("NLPStatus");
        if (DocumentRows.present(status)) {
            var extension = document.getExtensionByUrl(NLP_EXTENSION);
            if (extension == null) extension = document.addExtension().setUrl(NLP_EXTENSION).setValue(new CodeableConcept());
            ((CodeableConcept)extension.getValue()).addCoding().setSystem(NLP_SYSTEM).setCode(status);
        }
    }

    /** Informational diagnostics; explicit codes are never changed or discarded. */
    public static List<String> nlpIssues(DocumentReference document) {
        var extension = document.getExtensionByUrl(NLP_EXTENSION);
        if (extension == null) return List.of();
        var codes = ((CodeableConcept)extension.getValue()).getCoding().stream()
                .map(org.hl7.fhir.r4.model.Coding::getCode).collect(java.util.stream.Collectors.toSet());
        List<String> issues = new java.util.ArrayList<>();
        for (String code : codes) {
            if (!NLP_CODES.contains(code)) issues.add("Unknown NLPStatus: " + code);
            String parent = java.util.Set.of("format-change", "content-change").contains(code) ? "preprocessed"
                    : java.util.Set.of("preanno", "deid", "semantic").contains(code) ? "annotated" : null;
            if (parent != null && !codes.contains(parent)) issues.add("NLPStatus " + code + " requires " + parent);
        }
        return issues;
    }

    /**
     * @param record
     * @param previousRecordPID
     * @param result
     * @param validator
     * @param options
     * @throws Exception
     */
    public DocumentReferenceConverter(CSVRecord record, String previousRecordPID, ConverterResult result,
            FHIRValidator validator, ConverterOptions options) throws Exception {
        super(record, previousRecordPID, result, validator, options);
    }

    @Override
    protected List<Resource> convertInternal() throws Exception {
        String sourceId = value(DocumentRows.ID);
        if (!DocumentRows.present(sourceId)) {
            var errors = DocumentRows.continuationErrors(this::value);
            if (!errors.isEmpty()) throw new IllegalArgumentException(String.join("; ", errors));
            if (result.currentDocumentReference == null)
                throw new IllegalArgumentException("Continuation requires a preceding document with Dokument-ID");
            appendCodings(result.currentDocumentReference);
            return List.of();
        }
        result.currentDocumentReference = null;
        var errors = new DocumentRows().accept(this::value);
        if (!errors.isEmpty()) throw new IllegalArgumentException(String.join("; ", errors));
        DocumentReference documentReference = new DocumentReference();
        int nextId = result.getNextId(DocumentReference, DocumentReference.class, START_ID_DOCUMENT_REFERENCE);
        String encounterId = getEncounterId();
        String id = (isBlank(encounterId) ? getPatientId() : encounterId) + ResourceIdSuffix.DOCUMENT_REFERENCE
                + nextId;
        documentReference.setId(id);
        documentReference.setSubject(getPatientReference());
        String timestamp = ClinicalValues.get(this, ClinicalValues.Column.Ausgabezeitpunkt);
        documentReference.setDateElement(ClinicalValues.instant(timestamp));
        String status = ClinicalValues.get(this, ClinicalValues.Column.Status);
        if (status != null) documentReference.setStatusElement(InputCode.of(documentReference.getStatusElement(), status));

        appendCodings(documentReference);

        // TODO: Enable DocumentResource securityLabel filling by excel input data
        CodeableConcept securityLabel1 = createCodeableConcept(
                "http://terminology.hl7.org/CodeSystem/v3-Confidentiality", "L", "low", null, "4.0.1");
        CodeableConcept securityLabel2 = createCodeableConcept("http://terminology.hl7.org/CodeSystem/v3-ActReason",
                "HTEST", "test health data", null, "4.0.1");
        documentReference.setSecurityLabel(List.of(securityLabel1, securityLabel2));

        DocumentReferenceContextComponent context = new DocumentReferenceContextComponent();
        context.setEncounter(getEncounterReferences());
        documentReference.setContext(context);

        boolean embed = "Ja".equalsIgnoreCase(get(Embed));
        String filePath = get(Dateipfad);
        String text = ClinicalValues.get(this, ClinicalValues.Column.Dokumenttext);
        Attachment attachment = !embed || text == null ? createAttachment(Paths.get(filePath), embed)
                : new Attachment().setContentType("text/plain; charset=utf-8")
                    .setData(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        if (!attachment.isEmpty()) documentReference.setContent(singletonList(new DocumentReferenceContentComponent(attachment)));
        result.currentDocumentReference = documentReference;
        return singletonList(documentReference);
    }

    /**
     * Create Attachment from file
     *
     * @param path  path to file
     * @param embed if <code>true</code> embed file as binary else just add URL
     * @return new FHIR Attachment
     * @throws IOException
     */
    public static Attachment createAttachment(Path path, boolean embed) throws IOException {
        Attachment attachment = new Attachment();
        if (path == null) return attachment;
        File file = path.toFile();
        if (embed) {
            if (file.isFile() && file.canRead()) {
                // Read unlimited
                byte[] bytes = Files.readAllBytes(path);
                attachment.setData(bytes);
                // optional
                attachment.setSize(bytes.length);
            } else {
                attachment.getDataElement().addExtension(DATA_ABSENT_REASON_ERROR);
            }
        } else {
            attachment.setUrl(path.toUri().toURL().toExternalForm());
        }
        String contentType = getContentType(file);
        // available type:
        // https://terminology.hl7.org/1.0.0/CodeSystem-v3-mediatypes.html
        attachment.setContentType(contentType);

        // optional
        Path fileName = path.getFileName();
        if (fileName != null) {
            attachment.setTitle(fileName.toString());
        }
        // When attachment was first created
        // For now: take file creation time
        // Better (?): date, when document was written
        if (embed && file.isFile() && file.canRead()) {
            attachment.setCreation(new Date(((FileTime) Files.getAttribute(path, "creationTime")).toMillis()));
        }
        return attachment;
    }

    /**
     * @param file
     * @return
     */
    public static String getContentType(File file) {
        return URLConnection.guessContentTypeFromName(file.getName());
    }

}
