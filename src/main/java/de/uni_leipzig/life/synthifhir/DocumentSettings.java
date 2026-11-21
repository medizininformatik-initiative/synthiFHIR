package de.uni_leipzig.life.synthifhir;

import java.util.List;
import org.hl7.fhir.r4.model.CodeableConcept;
import org.hl7.fhir.r4.model.Coding;
import org.hl7.fhir.r4.model.DocumentReference;
import org.hl7.fhir.r4.model.Resource;
import de.uni_leipzig.life.synthifhir.converter.DocumentReferenceConverter;

/** Apply document lists after the complete source document block has been collected. */
final class DocumentSettings {
    static void apply(List<Resource> resources, ContractConfiguration config) {
        if (config == null) return;
        for (Resource resource : resources) {
            if (!(resource instanceof DocumentReference)) continue;
            DocumentReference document = (DocumentReference)resource;
            String typeMode = config.stored("document.type.mode").asText();
            if (typeMode.equals("replace") || typeMode.equals("fill") && !document.hasType()) {
                CodeableConcept type = new CodeableConcept();
                for (var row : config.stored("document.type.values")) {
                    Coding coding = new Coding();
                    if (row.has("system")) coding.setSystem(row.get("system").asText());
                    if (row.has("code")) coding.setCode(row.get("code").asText());
                    if (row.has("display")) coding.setDisplay(row.get("display").asText());
                    if (!coding.isEmpty()) type.addCoding(coding);
                }
                document.setType(type);
            }
            String mode = config.stored("document.nlp.mode").asText();
            String url = DocumentReferenceConverter.NLP_EXTENSION;
            boolean missing = document.getExtensionsByUrl(url).stream().noneMatch(extension -> extension.hasValue());
            if (mode.equals("omit") || mode.equals("replace") || mode.equals("fill") && missing) {
                document.getExtension().removeIf(extension -> url.equals(extension.getUrl()));
                if (!mode.equals("omit")) {
                    CodeableConcept statuses = new CodeableConcept();
                    for (var code : config.stored("document.nlp.values"))
                        if (!code.asText().isBlank()) statuses.addCoding(new Coding(DocumentReferenceConverter.NLP_SYSTEM, code.asText(), null));
                    if (!statuses.isEmpty()) document.addExtension(url, statuses);
                }
            }
        }
    }
}
