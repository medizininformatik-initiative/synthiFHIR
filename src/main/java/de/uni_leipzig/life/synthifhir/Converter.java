package de.uni_leipzig.life.synthifhir;

import static com.google.common.base.Strings.isNullOrEmpty;
import static de.uni_leipzig.life.synthifhir.BundleFunctions.createReference;
import static de.uni_leipzig.life.synthifhir.TableColumnIdentifier.isMandatory;
import static de.uni_leipzig.life.synthifhir.TableIdentifier.Fall;
import static de.uni_leipzig.life.synthifhir.TableIdentifier.Person;
import static de.uni_leipzig.life.synthifhir.utils.DateUtil.parseDateType;
import static java.util.regex.Pattern.CASE_INSENSITIVE;
import static org.apache.logging.log4j.util.Strings.isBlank;
import static org.hl7.fhir.r4.model.codesystems.DataAbsentReason.ERROR;
import static org.hl7.fhir.r4.model.codesystems.DataAbsentReason.NOTAPPLICABLE;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.annotation.Nullable;

import org.apache.commons.csv.CSVParser;
import org.apache.commons.csv.CSVRecord;
import org.hl7.fhir.r4.model.CodeSystem;
import org.hl7.fhir.r4.model.CodeType;
import org.hl7.fhir.r4.model.CodeableConcept;
import org.hl7.fhir.r4.model.Coding;
import org.hl7.fhir.r4.model.DateTimeType;
import org.hl7.fhir.r4.model.DateType;
import org.hl7.fhir.r4.model.Encounter;
import org.hl7.fhir.r4.model.Extension;
import org.hl7.fhir.r4.model.Factory;
import org.hl7.fhir.r4.model.Patient;
import org.hl7.fhir.r4.model.Period;
import org.hl7.fhir.r4.model.Reference;
import org.hl7.fhir.r4.model.Resource;
import org.hl7.fhir.r4.model.codesystems.DataAbsentReason;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.google.common.base.Strings;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableSet;

import de.uni_leipzig.imise.utils.Excel2Csv;
import de.uni_leipzig.imise.utils.StringUtils;
import de.uni_leipzig.imise.utils.Sys;
import de.uni_leipzig.imise.validate.FHIRValidator;
import de.uni_leipzig.life.synthifhir.TableIdentifier.DefaultTableColumnNames;
import de.uni_leipzig.life.synthifhir.converter.ResourceIdSuffix;
import de.uni_leipzig.life.synthifhir.utils.DateUtil;

/**
 * @author fheuschkel (02.11.2020), AXS (18.11.2021)
 */
public abstract class Converter {

    /**  */
    protected static Logger LOG = LoggerFactory.getLogger(Converter.class);

    /** Char of whitesace */
    private static final char WHITE_SPACE = (char) 32;

    /** An empty list as default return value for empty records */
    private static final List<? extends Resource> EMPTY_RESOURCE_LIST = ImmutableList.of();

    /**
     * Specifies how to handle missing values.
     */
    public static enum EmptyRecordValueErrorLevel {
        /** Throw error on missing value */
        ERROR,
        /** Print warning on missing value */
        WARNING,
        /** Ignore missing value */
        IGNORE,
    }

    /**  */
    public static final Extension DATA_ABSENT_REASON_NOTAPPLICABLE = createDataAbsentReason(NOTAPPLICABLE);

    /**  */
    public static final Extension DATA_ABSENT_REASON_ERROR = createDataAbsentReason(ERROR);

    /**
     * All strings as a grep pattern that can be interpreted as 'yes' in English
     * and German.
     */
    public static final Set<String> GENERAL_YES_VALUES = ImmutableSet.of("ja", "j", "1", "y", "yes", "true", "x");

    /**  */
    final String pid;

    /**  */
    final List<String> encounterIDs;

    /**  */
    private final CSVRecord record;

    /**  */
    protected final ConverterResult result;

    /**
     * Validator to validate resources. Can be <code>null</code>. Can be
     * <code>null</code> if nothing should be validated.
     */
    protected final FHIRValidator validator;

    /** The Converteroptions with the */
    protected final ConverterOptions options;

    /**
     * The enum with the column identifiers (initialized by refelection).
     */
    private final Class<? extends Enum<? extends TableColumnIdentifier>> columnIdentifiersClass;

    /**
     * @param record
     * @param previousRecordPID
     * @param result
     * @param validator         Validator to validate resources. Can be
     *                          <code>null</code> if nothing should be validated.
     * @param options
     * @throws Exception
     */
    public Converter(CSVRecord record, String previousRecordPID, ConverterResult result,
            @Nullable FHIRValidator validator, ConverterOptions options) throws Exception {
        this.record = record;
        this.result = result;
        this.validator = validator;
        this.options = options;
        pid = parsePatientId(previousRecordPID);
        encounterIDs = parseEncounterIds();
        columnIdentifiersClass = reflectColumnIdentifiersClass();
    }

    /**
     * @return
     * @throws Exception
     */
    public List<? extends Resource> convert() throws Exception {
        if (isEmptyCSVRecord()) {
            return EMPTY_RESOURCE_LIST;
        }
        var resources = convertInternal();
        var context = new ConverterResult.InputContext(pid, List.copyOf(encounterIDs), record.getRecordNumber(),
                de.uni_leipzig.life.synthifhir.converter.ClinicalValues.get(this,
                        de.uni_leipzig.life.synthifhir.converter.ClinicalValues.Column.Ausgabezeitpunkt) != null);
        for (Resource resource : resources) result.recordInput(resource, context);
        return resources;
    }

    /**
     * @return
     * @throws Exception
     */
    protected abstract List<? extends Resource> convertInternal() throws Exception;

    /**
     * Checks if the records contains no (valid) values. A value is valid if it is
     * not null and not only consists of whitespace characters or minus signs '-'.
     *
     * @return <code>true</code> if the record contains no values.
     */
    private boolean isEmptyCSVRecord() {
        boolean isEmpty = true;
        try (CSVParser parser = record.getParser()) {
            for (String columnIdentifier : parser.getHeaderNames()) {
                String value = record.get(columnIdentifier);
                if (value != null && !isBlank(value.replace('-', WHITE_SPACE))) {
                    isEmpty = false;
                    break;
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
        return isEmpty;
    }

    /**
     * @return The class with the enum with the definition of the table columns
     */
    public Class<? extends Enum<? extends TableColumnIdentifier>> getColumnIdentifiersClass() {
        return columnIdentifiersClass;
    }

    /**
     * @return The class with the enum with the definition of the table columns
     */
    private Class<? extends Enum<? extends TableColumnIdentifier>> reflectColumnIdentifiersClass() {
        return reflectColumnIdentifiersClass(getClass());
    }

    /**
     * @param converterClass
     * @return The class with the enum with the definition of the table columns in
     *         the passed converter class
     */
    @SuppressWarnings("unchecked") // it's checked
    public static Class<? extends Enum<? extends TableColumnIdentifier>> reflectColumnIdentifiersClass(
            Class<? extends Converter> converterClass) {
        for (Class<?> declaredClass : converterClass.getDeclaredClasses()) {
            if (Enum.class.isAssignableFrom(declaredClass)
                    && TableColumnIdentifier.class.isAssignableFrom(declaredClass)) {
                return (Class<? extends Enum<? extends TableColumnIdentifier>>) declaredClass.asSubclass(Enum.class);
            }
        }
        return null;
    }

    /**
     * @param msg
     * @throws Exception
     */
    public void error(String msg) throws Exception {
        throw new Exception("Error on " + getLogMessageBody(msg));
    }

    /**
     * @param msg
     */
    public void err(String msg) {
        err(msg, 1);
    }

    /**
     * @param msg
     */
    protected void err(String msg, int stackTraceBackwardSteps) {
        LOG.error(getLogMessageBody(msg) + "     " + Sys.getStackTraceStep(3 + stackTraceBackwardSteps));
    }

    /**
     * @param msg
     */
    public void warning(String msg) {
        warning(msg, 1);
    }

    /**
     * @param msg
     */
    protected void warning(String msg, int stackTraceBackwardSteps) {
        LOG.warn(getLogMessageBody(msg) + "     " + Sys.getStackTraceStep(3 + stackTraceBackwardSteps));
    }

    /**
     * @param msg
     */
    public void info(String msg) {
        info(msg, 1);
    }

    /**
     * @param msg
     */
    protected void info(String msg, int stackTraceBackwardSteps) {
        LOG.info(getLogMessageBody(msg) + "     " + Sys.getStackTraceStep(3 + stackTraceBackwardSteps));
    }

    /**
     * @param msg
     * @return
     */
    protected String getLogMessageBody(String msg) {
        return getClass().getSimpleName().replaceFirst("Converter", "") + ": " + msg + ":" + record.getRecordNumber()
                + "! "
                + record.toString();
    }

    /**
     * @return
     */
    public String getPatientId() {
        return pid;
    }

    /**
     * @return
     * @throws Exception
     */
    public final String getEncounterId() throws Exception {
        return encounterIDs.isEmpty() ? null : encounterIDs.get(0);
    }

    /**
     * @return
     * @throws Exception
     */
    public final List<String> getEncounterIds() throws Exception {
        return encounterIDs;
    }

    /**
     * Checks whether the input record maps the named column.
     *
     * @param name column name
     * @return whether the column is mapped
     */
    public boolean hasColumn(String name) {
        return record.isMapped(name);
    }

    public String get(Object columnIdentifier) {
        String columnName = Objects.toString(columnIdentifier, null);
        boolean tryCatch = columnIdentifier instanceof TableColumnIdentifier
                && !((TableColumnIdentifier) columnIdentifier).isMandatory();
        String entry = null;
        if (tryCatch) {
            try {
                entry = record.get(columnName);
            } catch (Exception e) {
                entry = "";
            }
        } else {
            entry = record.get(columnName);
        }
        return entry;
    }

    /**
     * @param previousRecordPID
     * @return
     * @throws Exception
     */
    protected String parsePatientId(String previousRecordPID) throws Exception {
        TableColumnIdentifier patientIDColumnIdentifier = getPatientIDColumnIdentifier();
        String id = get(patientIDColumnIdentifier);
        if (Strings.isNullOrEmpty(id)) {
            id = previousRecordPID;
        }
        if (id != null) {
            return options.getFullPID(id);
        }
        error(patientIDColumnIdentifier + " empty for Record");
        return null;
    }

    /** Read explicit encounter numbers; a missing or empty column leaves the association absent. */
    private List<String> parseEncounterIds() throws Exception {
        TableColumnIdentifier column = getMainEncounterNumberColumnIdentifier();
        if (column == null || !record.isMapped(column.toString())) return Collections.emptyList();
        String value = record.get(column.toString());
        if (isBlank(value)) return Collections.emptyList();
        List<String> encounterNumbers = StringUtils.parseList(value, ",");
        for (int i = encounterNumbers.size() - 1; i >= 0; i--) {
            encounterNumbers.set(i, pid + ResourceIdSuffix.ENCOUNTER_LEVEL_1 + encounterNumbers.get(i));
        }
        return encounterNumbers;
    }

    /**
     * @return the enum identifier for the column with the patient ID
     */
    protected TableColumnIdentifier getPatientIDColumnIdentifier() {
        return DefaultTableColumnNames.Patient_ID;
    }

    /**
     * @return the enum identifier for the column with the patient ID
     */
    protected TableColumnIdentifier getMainEncounterNumberColumnIdentifier() {
        return DefaultTableColumnNames.Fall_Nr;
    }

    /**
     * @return
     * @throws Exception
     */
    protected Reference getPatientReference() throws Exception {
        return getPatientReference(false);
    }

    /**
     * @param checkExistence
     * @return
     * @throws Exception
     */
    protected Reference getPatientReference(boolean checkExistence) throws Exception {
        return getReference(checkExistence ? Person : null, pid, Patient.class);
    }

    /**
     * @return
     * @throws Exception
     */
    protected Reference getEncounterReference() throws Exception {
        return getEncounterReference(getEncounterId(), false);
    }

    /**
     * @param encounterId
     * @param checkExistence
     * @return
     * @throws Exception
     */
    protected Reference getEncounterReference(String encounterId, boolean checkExistence) throws Exception {
        if (encounterId == null) { // can be optional
            return null;
        }
        return getReference(checkExistence ? Fall : null, encounterId, Encounter.class);
    }

    /**
     * @return
     * @throws Exception
     */
    protected List<Reference> getEncounterReferences() throws Exception {
        List<Reference> encounterReferences = new ArrayList<>(encounterIDs.size());
        for (String encounterId : encounterIDs) {
            encounterReferences.add(getEncounterReference(encounterId, false));
        }
        return encounterReferences;
    }

    /**
     * @param tableIdentifier
     * @param elementID
     * @param referenceType
     * @return
     * @throws Exception
     */
    private Reference getReference(TableIdentifier tableIdentifier, String elementID,
            Class<? extends Resource> referenceType) throws Exception {
        if (tableIdentifier != null) {
            Resource resource = result.get(tableIdentifier, referenceType, elementID); // must exists in this bundle to
                                                                                       // create a valid reference
            if (resource == null) {
                return null;
            }
        }
        return createReference(referenceType, elementID);
    }

    /**
     * Creates a {@link CodeableConcept} from the columns with the given name of
     * the {@link CSVRecord} of this converter and the given codeSystemMapper.
     *
     * @param codeColumnName   Name of the column with the human readable code
     * @param codeSystemMapper a mapper that maps from the human readable code to a
     *                         code from a code system. This mapper must return the
     *                         code system via the function
     *                         {@link CodeSystemMapper#getCodeSystem()}
     * @return a new {@link CodeableConcept}
     * @throws Exception if the {@link CSVRecord} returns <code>null</code> for the
     *                   codeColumnName
     */
    public CodeableConcept createCodeableConcept(Enum<?> codeColumnName, CodeSystemMapper codeSystemMapper)
            throws Exception {
        String humanText = record.get(codeColumnName);
        if (humanText == null) {
            error(codeColumnName + " empty for Record");
            return null;
        }
        String code = codeSystemMapper.getHumanToCode(humanText);
        if (code == null) {
            code = humanText;
        }
        String codeSystem = codeSystemMapper.getCodeSystem();
        Coding coding = createCoding(codeSystem, code, humanText);
        return new CodeableConcept(coding);
    }

    /**
     * Creates a new {@link CodeableConcept} to which coding is added. This code
     * has the passed {@link CodeSystem} and as code value the value from the
     * column with the name codeColumnName from the {@link CSVRecord}. Additionally
     * the returned {@link CodeableConcept} gets the text from the column
     * textColumnName.
     *
     * @param codeSystem     the code system of the contained {@link Coding}
     * @param codeColumnName Name of the column with the code value for the
     *                       {@link Coding}
     * @param textColumnName Name of the column with the text for the returned
     *                       {@link CodeableConcept}
     * @return the supplied code and text, or <code>null</code> when both are empty
     */
    public CodeableConcept createCodeableConcept(String codeSystem, Enum<?> codeColumnName, Enum<?> textColumnName) {
        String code = record.get(codeColumnName);
        CodeableConcept concept = new CodeableConcept().setText(record.get(textColumnName));
        if (!isBlank(code)) concept.addCoding(createCoding(codeSystem, code));
        return concept.isEmpty() ? null : concept;
    }

    /**
     * Creates an new {@link CodeableConcept} with the given {@link Coding}.
     * Additionally the returned {@link CodeableConcept} gets the text from the
     * column textColumnName.
     *
     * @param coding         the {@link Coding} to set for the returned
     *                       {@link CodeableConcept}
     * @param textColumnName Name of the column with the text for the returned
     *                       {@link CodeableConcept}
     * @return a new {@link CodeableConcept}
     */
    public CodeableConcept createCodeableConcept(Coding coding, Enum<?> textColumnName) {
        String text = record.get(textColumnName);
        return new CodeableConcept(coding).setText(text);
    }

    /**
     * Creates a new {@link CodeableConcept} with the given code and code system
     * for the contained {@link Coding}.
     *
     * @param codeSystem the code system of the contained {@link Coding}
     * @param code       the code of the contained {@link Coding}
     * @return a new {@link CodeableConcept}
     */
    public static CodeableConcept createCodeableConcept(String codeSystem, String code) {
        Coding coding = createCoding(codeSystem, code);
        return new CodeableConcept(coding);
    }

    /**
     * Creates a new {@link CodeableConcept} with the given code and code system
     * for the contained {@link Coding}. Additionally the returned
     * {@link CodeableConcept} gets the text from parameter <code>text</code>.
     *
     * @param codeSystem the code system of the contained {@link Coding}
     * @param code       the code of the contained {@link Coding}
     * @param text
     * @return a new {@link CodeableConcept}
     */
    public static CodeableConcept createCodeableConcept(String codeSystem, String code, String text) {
        return createCodeableConcept(codeSystem, code, null, text);
    }

    /**
     * Creates a new {@link CodeableConcept} with the given code, code system and
     * display for the contained {@link Coding}. Additionally the returned
     * {@link CodeableConcept} gets the text from parameter <code>text</code>.
     *
     * @param codeSystem the code system of the contained {@link Coding}
     * @param code       the code of the contained {@link Coding}
     * @param display
     * @param text
     * @return a new {@link CodeableConcept}
     */
    public static CodeableConcept createCodeableConcept(String codeSystem, String code, String display, String text) {
        Coding coding = createCoding(codeSystem, code, display);
        return new CodeableConcept(coding).setText(text);
    }

    /**
     * Creates a new {@link CodeableConcept} with the given code, code system and
     * display for the contained {@link Coding}. Additionally the returned
     * {@link CodeableConcept} gets the text from parameter <code>text</code>.
     *
     * @param codeSystem the code system of the contained {@link Coding}
     * @param code       the code of the contained {@link Coding}
     * @param display
     * @param text
     * @param version
     * @return a new {@link CodeableConcept}
     */
    public static CodeableConcept createCodeableConcept(String codeSystem, String code, String display, String text,
            String version) {
        Coding coding = createCoding(codeSystem, code, display);
        coding.setVersion(version);
        return new CodeableConcept(coding).setText(text);
    }

    /**
     * Creates a new {@link Coding} with the given parameters.
     *
     * @param codeSystem     the code system to set
     * @param codeColumnName Name of the column with the code
     * @return a new {@link Coding}
     */
    public Coding createCoding(String codeSystem, Enum<?> codeColumnName) {
        String code = record.get(codeColumnName);
        if (isBlank(code)) return null;
        return createCoding(codeSystem, code);
    }

    /**
     * @param codeSystem the code system to set
     * @param code       the code to set
     * @return a new {@link Coding} with the given values
     */
    public static Coding createCoding(String codeSystem, String code) {
        Coding coding = new Coding().setSystem(codeSystem);
        if (!isBlank(code)) {
            Extension absent = de.uni_leipzig.life.synthifhir.converter.DiagnosisValues.absentReason(code);
            if (absent == null) coding.setCode(code);
            else coding.getCodeElement().addExtension(absent);
            return coding;
        }
        return coding;
    }

    /**
     * @param codeSystem the code system to set
     * @param code       the code to set
     * @param display    the display text to set for the returned {@link Coding}
     * @return a new {@link Coding} with the given values
     */
    public static Coding createCoding(String codeSystem, String code, String display) {
        Coding coding = createCoding(codeSystem, code);
        coding.setDisplay(display);
        return coding;
    }

    /**
     * @param codeSystem the code system to set
     * @param code       the code to set
     * @param display    the display text to set for the returned {@link Coding}
     * @param version
     * @return a new {@link Coding} with the given values
     */
    public static Coding createCoding(String codeSystem, String code, String display, String version) {
        Coding coding = createCoding(codeSystem, code, display);
        coding.setVersion(version);
        return coding;
    }

    /**
     * @param coding
     * @return
     */
    public boolean isDataAbsentReason(Coding coding) {
        return coding != null && "http://terminology.hl7.org/CodeSystem/data-absent-reason".equals(coding.getSystem());
    }

    /**
     * @param startDateColumnName
     * @param endDateColumnName
     * @return a {@link Period} object filled with the start and end date given by
     *         the column names in the {@link CSVRecord} of this converter
     */
    public Period createPeriod(Enum<?> startDateColumnName, Enum<?> endDateColumnName) throws Exception {
        String endDateValue = null;
        DateTimeType startDate = null;
        DateTimeType endDate = null;
        try {
            String startDateValue = record.get(startDateColumnName);
            startDate = DateUtil.parseDateTimeType(startDateValue);
        } catch (Exception e) {
            error("Can not parse " + startDateColumnName + " as date for Record " + record);
            return null;
        }
        try {
            endDateValue = record.get(endDateColumnName);
            endDate = DateUtil.parseDateTimeType(endDateValue);
        } catch (Exception e) {
            if (!isNullOrEmpty(endDateValue)) {
                error("Can not parse " + endDateColumnName + " as date for Record " + record);
                return null;
            }
        }
        return createPeriod(startDate, endDate);
    }

    /**
     * @param startDate
     * @param endDate
     * @return
     */
    public Period createPeriod(DateTimeType startDate, DateTimeType endDate) {
        return new Period().setStartElement(startDate).setEndElement(endDate);
    }

    /**
     * @return
     * @throws Exception
     */
    protected DateType parseDate(Enum<?> dateColumnName) throws Exception {
        String date = get(dateColumnName);
        if (date != null) {
            try {
                return parseDateType(date);
            } catch (Exception e) {
                error("Can not parse " + dateColumnName + " for Record");
                return null;
            }
        }
        if (dateColumnName instanceof TableColumnIdentifier && ((TableColumnIdentifier) dateColumnName).isMandatory()) {
            error(dateColumnName + " empty for Record");
        }
        return null;
    }

    /**
     * @return
     * @throws Exception
     */
    protected DateTimeType parseDateTimeType(Enum<?> dateColumnName) throws Exception {
        String date = get(dateColumnName);
        if (date != null) {
            try {
                return DateUtil.parseDateTimeType(date);
            } catch (Exception e) {
                error("Can not parse " + dateColumnName + " for Record");
                return null;
            }
        }
        if (dateColumnName instanceof TableColumnIdentifier && ((TableColumnIdentifier) dateColumnName).isMandatory()) {
            error(dateColumnName + " empty for Record");
        }
        return null;
    }

    /**
     * Return the toString() of the record of this converter.
     */
    @Override
    public String toString() {
        return record.toString();
    }

    /**
     * @return
     */
    private static Extension createDataAbsentReason(DataAbsentReason dataAbsentReason) {
        return Factory.newExtension("http://hl7.org/fhir/StructureDefinition/data-absent-reason",
                new CodeType(dataAbsentReason.toCode()), true);
    }

    /**
     * @param value
     * @return
     */
    public static final boolean isYesValue(String value) {
        return !isBlank(value) && GENERAL_YES_VALUES.contains(value.trim());
    }

    /**
     * @param valuePattern the toString() value from this enum is the pattern that
     *                     must match the value of the record in the column with
     *                     the columnName
     * @param columnName   name of the column with the value to match
     * @return <code>true</code> if the value matches the pattern
     */
    public final boolean matches(Enum<?> valuePattern, Enum<?> columnName) {
        String patternString = valuePattern.toString();
        Pattern pattern = Pattern.compile(patternString, CASE_INSENSITIVE);
        String value = get(columnName);
        Matcher matcher = pattern.matcher(value);
        return matcher.find();
    }

}
