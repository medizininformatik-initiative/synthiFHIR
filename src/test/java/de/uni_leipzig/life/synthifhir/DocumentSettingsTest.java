package de.uni_leipzig.life.synthifhir;

import static org.junit.Assert.*;
import java.util.List;
import org.hl7.fhir.r4.model.*;
import org.junit.Test;
import de.uni_leipzig.life.synthifhir.converter.DocumentReferenceConverter;

public class DocumentSettingsTest {
    private static final String URL = DocumentReferenceConverter.NLP_EXTENSION;
    private ContractConfiguration config(String modes) {
        return ContractConfiguration.parse("CONFIGURATION_VERSION=1\n" + modes
                + "\nDOCUMENT_TYPE_VALUES=[{\"system\":\"urn:free\",\"code\":\"a,b\",\"display\":\"Free\"},{\"code\":\"second\"}]"
                + "\nDOCUMENT_NLP_VALUES=[\"deid\",\"custom\",\"deid\"]\n");
    }
    @Test public void inputModePreservesSource() {
        var document = source();
        String before = OutputFileType.JSON.getParser().encodeResourceToString(document);
        DocumentSettings.apply(List.of(document), config(""));
        assertEquals(before, OutputFileType.JSON.getParser().encodeResourceToString(document));
    }
    private DocumentReference source() {
        var document = new DocumentReference();
        document.getType().addCoding(new Coding("urn:source", "existing", null));
        document.addExtension(URL, new CodeableConcept().addCoding(new Coding("urn:source", "unprocessed", null)));
        document.addExtension("urn:unrelated", new StringType("keep"));
        return document;
    }
    @Test public void fillOnlyReplacesEntirelyMissingLists() {
        var document = source();
        var empty = new DocumentReference();
        var textOnly = new DocumentReference();
        textOnly.getType().setText("supplied text");
        DocumentSettings.apply(List.of(document, empty, textOnly), config("DOCUMENT_TYPE_MODE=fill\nDOCUMENT_NLP_MODE=fill"));
        assertEquals("existing", document.getType().getCodingFirstRep().getCode());
        assertEquals(2, empty.getType().getCoding().size());
        assertEquals("supplied text", textOnly.getType().getText());
        assertEquals(3, ((CodeableConcept)empty.getExtensionByUrl(URL).getValue()).getCoding().size());
    }
    @Test public void replacementPreservesFreeValuesAndUnrelatedExtensions() {
        var document = source();
        DocumentSettings.apply(List.of(document), config("DOCUMENT_TYPE_MODE=replace\nDOCUMENT_NLP_MODE=replace"));
        assertEquals("a,b", document.getType().getCodingFirstRep().getCode());
        var codes = ((CodeableConcept)document.getExtensionByUrl(URL).getValue()).getCoding();
        assertEquals(List.of("deid", "custom", "deid"), codes.stream().map(Coding::getCode).toList());
        assertEquals(DocumentReferenceConverter.NLP_SYSTEM, codes.get(0).getSystem());
        assertNotNull(document.getExtensionByUrl("urn:unrelated"));
        DocumentSettings.apply(List.of(document), config("DOCUMENT_NLP_MODE=omit"));
        assertNull(document.getExtensionByUrl(URL));
        assertNotNull(document.getExtensionByUrl("urn:unrelated"));
    }
    @Test public void defaultPropertiesAreExecutableAndSupported() {
        assertTrue(ContractConfiguration.parse(ContractConfiguration.defaultProperties()).unsupportedSettings().isEmpty());
        assertTrue(config("DOCUMENT_TYPE_MODE=replace").unsupportedSettings().isEmpty());
    }
    @Test(expected = IllegalArgumentException.class) public void malformedListsAreRejected() {
        ContractConfiguration.parse("CONFIGURATION_VERSION=1\nDOCUMENT_TYPE_VALUES=[{\"code\":17}]");
    }
}
