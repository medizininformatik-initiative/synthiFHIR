package de.uni_leipzig.imise.validate;

import static de.uni_leipzig.imise.validate.TemplateValidationIssue.Severity.ERROR;
import static de.uni_leipzig.imise.validate.TemplateValidationIssue.Severity.WARNING;
import static de.uni_leipzig.life.synthifhir.ConverterOptions.BooleanOption.CHECK_INPUT_CONSISTENCY;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.Date;

import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellType;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.FormulaEvaluator;
import org.apache.poi.ss.usermodel.CellValue;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.DateUtil;
import org.apache.poi.xssf.usermodel.XSSFSheet;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.hl7.fhir.r4.model.DateTimeType;
import org.hl7.fhir.r4.model.Coding;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import de.uni_leipzig.imise.validate.TemplateValidationIssue.Severity;
import de.uni_leipzig.life.synthifhir.ConverterOptions;
import de.uni_leipzig.life.synthifhir.converter.DiagnosisValues;
import de.uni_leipzig.life.synthifhir.converter.AdmissionReasonValues;

/**
 * Validates the current Excel input template contract before converting it.
 */
public class ExcelTemplateValidator {

    private static final Logger LOG = LoggerFactory.getLogger(ExcelTemplateValidator.class);

    private static final Map<String, List<String>> EXPECTED_HEADERS = createExpectedHeaders();

    private final DataFormatter formatter = new DataFormatter(Locale.GERMANY);

    private FormulaEvaluator formulaEvaluator;

    public TemplateValidationResult validate(File excelFile) throws IOException {
        TemplateValidationResult result = new TemplateValidationResult();
        for (var set : de.uni_leipzig.life.synthifhir.ConverterOptionSet.defaults()) {
            var checked = validate(excelFile, set.options(), set.name());
            if (checked.hasErrors()) return checked;
            result = checked;
        }
        return result;
    }

    public TemplateValidationResult validate(File excelFile, ConverterOptions options) throws IOException {
        return validate(excelFile, options, "Konvertierungsoptionen");
    }

    public TemplateValidationResult validate(File excelFile, ConverterOptions options, String optionSetName) throws IOException {
        try (XSSFWorkbook workbook = de.uni_leipzig.imise.utils.ExcelWorkbooks.openReadOnly(excelFile)) {
            return validate(workbook, options, optionSetName);
        }
    }

    /** Validates a caller-owned workbook without closing or modifying it. */
    public TemplateValidationResult validate(XSSFWorkbook workbook, ConverterOptions options, String optionSetName) {
        try {
            formulaEvaluator = workbook.getCreationHelper().createFormulaEvaluator();
            Map<String, TableView> tables = new LinkedHashMap<>();
            for (String name : EXPECTED_HEADERS.keySet()) {
                var sheet = workbook.getSheet(name);
                if (sheet != null) tables.put(name, new ExcelTable(sheet));
            }
            return validateTables(tables, options, optionSetName);
        } finally {
            // The evaluator retains the workbook and its cell graph, even after close().
            formulaEvaluator = null;
        }
    }

    /** Applies the same input rules to UTF-8 CSV tables without constructing a workbook. */
    public TemplateValidationResult validateCsv(java.nio.file.Path directory, String prefix,
            ConverterOptions options, String optionSetName) throws IOException {
        Map<String, TableView> tables = new LinkedHashMap<>();
        for (String name : EXPECTED_HEADERS.keySet()) {
            var file = directory.resolve(prefix + name + ".csv");
            if (!java.nio.file.Files.exists(file)) continue;
            List<RowView> rows = new ArrayList<>();
            try (var reader = java.nio.file.Files.newBufferedReader(file, java.nio.charset.StandardCharsets.UTF_8);
                    var parser = org.apache.commons.csv.CSVFormat.DEFAULT.parse(reader)) {
                int width = -1;
                for (var record : parser) {
                    if (width < 0) width = record.size();
                    if (record.size() != width) throw new IOException("CSV column count differs from header: " + file
                            + " record " + record.getRecordNumber());
                    List<CellView> cells = new ArrayList<>();
                    for (String value : record) cells.add(new TextCell(cells.size(), value));
                    rows.add(new TextRow(rows.size(), cells));
                }
            }
            tables.put(name, new TextTable(name, rows));
        }
        return validateTables(tables, options, optionSetName);
    }

    private TemplateValidationResult validateTables(Map<String, TableView> tables, ConverterOptions options,
            String optionSetName) {
        TemplateValidationResult result = new TemplateValidationResult();
        try {
            for (String error : options.getErrors()) add(result, ERROR, optionSetName, 0, "A", error);
            boolean checkInputConsistency = options.is(CHECK_INPUT_CONSISTENCY);
            validateHeaders(tables, result);
            if (!checkInputConsistency) {
                LOG.info("Excel input consistency checks are disabled by {}", CHECK_INPUT_CONSISTENCY);
                return result;
            }
            Set<String> patientIds = validatePatients(tables, result);
            Set<String> encounterIds = validateEncounters(tables, result, patientIds);
            validateReferenceTables(tables, result, patientIds, encounterIds);
            return result;
        } finally {
            log(result);
        }
    }

    public void validateAndThrow(File excelFile) throws IOException {
        TemplateValidationResult result = validate(excelFile);
        if (result.hasErrors()) {
            throw new TemplateValidationException(result);
        }
    }

    private void validateHeaders(Map<String, TableView> workbook, TemplateValidationResult result) {
        for (Map.Entry<String, List<String>> entry : EXPECTED_HEADERS.entrySet()) {
            String sheetName = entry.getKey();
            TableView sheet = workbook.get(sheetName);
            if (sheet == null) {
                add(result, ERROR, sheetName, 0, null, "Required sheet is missing");
                continue;
            }
            List<String> actualHeaders = readHeaders(sheet);
            if (actualHeaders.size() != entry.getValue().size()
                    || !new HashSet<>(actualHeaders).equals(new HashSet<>(entry.getValue()))) {
                add(result, ERROR, sheetName, 1, null,
                        "Header does not match current template schema. Expected " + entry.getValue() + " but found "
                                + actualHeaders);
            }
        }
    }

    private Set<String> validatePatients(Map<String, TableView> workbook, TemplateValidationResult result) {
        Set<String> patientIds = new HashSet<>();
        TableView sheet = workbook.get("Person");
        if (sheet == null) {
            return patientIds;
        }
        Map<String, Integer> columns = columnIndexes(sheet);
        for (RowView row : sheet) {
            int rowIndex = row.getRowNum();
            if (rowIndex == 0) continue;
            if (isEmptyDataRow(row, columns)) {
                continue;
            }
            String patientId = get(row, columns, "Patient-ID");
            if (isBlank(patientId)) {
                add(result, ERROR, "Person", rowIndex + 1, "Patient-ID", "Patient-ID is required");
                continue;
            }
            if (!patientIds.add(patientId)) {
                add(result, ERROR, "Person", rowIndex + 1, "Patient-ID", "Patient-ID must be unique");
            }
            validateDateValue(sheet, row, columns, "Geburtsdatum", result, false);
            validateDateValue(sheet, row, columns, "Datum Einwilligung", result, false);
        }
        return patientIds;
    }

    private Set<String> validateEncounters(Map<String, TableView> workbook, TemplateValidationResult result,
            Set<String> patientIds) {
        Set<String> encounterIds = new HashSet<>();
        TableView sheet = workbook.get("Fall");
        if (sheet == null) {
            return encounterIds;
        }
        Map<String, Integer> columns = columnIndexes(sheet);
        var contacts = new de.uni_leipzig.life.synthifhir.converter.ContactInputValidator();
        String previousPatientId = null;
        for (RowView row : sheet) {
            int rowIndex = row.getRowNum();
            if (rowIndex == 0) continue;
            if (isEmptyDataRow(row, columns)) {
                continue;
            }
            String patientId = get(row, columns, "Patient-ID");
            if (isBlank(patientId)) {
                patientId = previousPatientId;
            } else {
                previousPatientId = patientId;
            }
            if (isBlank(patientId)) {
                add(result, ERROR, "Fall", rowIndex + 1, "Patient-ID", "Patient-ID or previous Patient-ID is required");
            } else if (!patientIds.contains(patientId)) {
                add(result, ERROR, "Fall", rowIndex + 1, "Patient-ID", "Patient-ID does not exist in Person sheet");
            }

            Map<String, String> contactValues = new HashMap<>();
            for (String column : columns.keySet()) contactValues.put(column, get(row, columns, column));
            for (String column : List.of("Start", "Ende")) {
                CellView cell = getCell(row, columns, column);
                if (cell != null && cell.hasDate()) contactValues.put(column, new DateTimeType(cell.date()).getValueAsString());
            }
            for (var issue : contacts.accept(new de.uni_leipzig.life.synthifhir.converter.ContactInputValidator.Input(
                    rowIndex + 1, patientId, contactValues))) {
                add(result, ERROR, "Fall", (int) issue.row(), issue.field(), issue.message());
            }
            String encounterNumber = get(row, columns, "Fall-Nr");
            if (!isBlank(patientId) && !isBlank(encounterNumber)) {
                validateRequired(sheet, row, columns, "Einrichtungskontaktklasse", result);
                encounterIds.add(patientId + "|" + encounterNumber);
            }
        }
        return encounterIds;
    }

    private void validateReferenceTables(Map<String, TableView> workbook, TemplateValidationResult result, Set<String> patientIds,
            Set<String> encounterIds) {
        for (String sheet : List.of("Impfung", "Befundbericht")) {
            validateReferenceTable(workbook, result, patientIds, encounterIds, sheet, List.of("Zeitpunkt", "Ende", "Ausgabezeitpunkt"));
        }
        validateReferenceTable(workbook, result, patientIds, encounterIds, "Behandlungsplan",
                List.of("Erstellungsdatum", "Beginn", "Ende", "Aktivitätsbeginn", "Aktivitätsende"));
        validateReferenceTable(workbook, result, patientIds, encounterIds, "Diagnose",
                List.of("Dokumentationszeitpunkt", "Beginn", "Ende"));
        validateReferenceTable(workbook, result, patientIds, encounterIds, "Prozedur",
                List.of("Durchführungsbeginn"));
        validateReferenceTable(workbook, result, patientIds, encounterIds, "Laborbefund",
                List.of("Zeitstempel (Abnahme)"));
        validateReferenceTable(workbook, result, patientIds, encounterIds, "Klinische Dokumentation",
                List.of("Zeitstempel"));
        validateReferenceTable(workbook, result, patientIds, encounterIds, "DocumentReference", List.of());
        validateReferenceTable(workbook, result, patientIds, encounterIds, "Medikation",
                List.of("Dokumentationszeitpunkt", "Beginn", "Ende"));
    }

    private void validateReferenceTable(Map<String, TableView> workbook, TemplateValidationResult result, Set<String> patientIds,
            Set<String> encounterIds, String sheetName, List<String> dateTimeColumns) {
        TableView sheet = workbook.get(sheetName);
        if (sheet == null) {
            return;
        }
        Map<String, Integer> columns = columnIndexes(sheet);
        String previousPatientId = null;
        Set<String> entryIds = new HashSet<>();
        var documents = new de.uni_leipzig.life.synthifhir.converter.DocumentRows();
        for (RowView row : sheet) {
            int rowIndex = row.getRowNum();
            if (rowIndex == 0) continue;
            if (isEmptyDataRow(row, columns)) {
                continue;
            }
            if ("DocumentReference".equals(sheetName)) {
                var errors = documents.accept(field -> get(row, columns, field));
                for (String error : errors) add(result, ERROR, sheetName, rowIndex + 1, "Dokument-ID", error);
                if (!errors.isEmpty()) continue;
            }
            String patientId = get(row, columns, "Patient-ID");
            if (isBlank(patientId)) {
                patientId = previousPatientId;
            } else {
                previousPatientId = patientId;
            }
            if (isBlank(patientId)) {
                add(result, ERROR, sheetName, rowIndex + 1, "Patient-ID",
                        "Patient-ID or previous Patient-ID is required");
            } else if (!patientIds.contains(patientId)) {
                add(result, ERROR, sheetName, rowIndex + 1, "Patient-ID",
                        "Patient-ID does not exist in Person sheet");
            }
            for (String encounterNumber : getEncounterNumbers(row, columns)) {
                if (!isBlank(patientId) && !encounterIds.contains(patientId + "|" + encounterNumber)) {
                    add(result, WARNING, sheetName, rowIndex + 1, "Fall-Nr",
                            "Fall-Nr does not exist for this Patient-ID in Fall sheet");
                }
            }
            if ("Behandlungsplan".equals(sheetName)) {
                for (String error : de.uni_leipzig.life.synthifhir.converter.CarePlanConverter.continuationErrors(
                        field -> get(row, columns, field)))
                    add(result, ERROR, sheetName, rowIndex + 1, "Patient-ID", error);
            }
            if ("Diagnose".equals(sheetName)) {
                validateDiagnosisSelections(row, columns, result);
            }
            if ("Medikation".equals(sheetName)) {
                for (String error : de.uni_leipzig.life.synthifhir.converter.MedicationValues.errors(key -> {
                    CellView cell = getCell(row, columns, key);
                    if (cell != null && cell.hasDate()) return new DateTimeType(cell.date()).getValueAsString();
                    return get(row, columns, key);
                })) {
                    add(result, ERROR, sheetName, rowIndex + 1, "Medikation", error);
                }
            }
            String idColumn = columns.containsKey("Eintrag ID") ? "Eintrag ID" : "Untersuchung ID";
            if (columns.containsKey(idColumn)) {
                String entryId = get(row, columns, idColumn);
                String parentId = get(row, columns, "Komponente von");
                if ("Eintrag ID".equals(idColumn) && isBlank(entryId)) {
                    add(result, ERROR, sheetName, rowIndex + 1, idColumn, "Entry ID is required");
                }
                if (!isBlank(parentId) && !entryIds.contains(patientId + "|" + parentId)) {
                    add(result, ERROR, sheetName, rowIndex + 1, "Komponente von", "Component parent must precede the component for the same patient");
                }
                if (!isBlank(entryId) && !entryIds.add(patientId + "|" + entryId)) {
                    add(result, ERROR, sheetName, rowIndex + 1, idColumn, "Duplicate entry ID for this patient");
                }
            }
            for (String columnName : dateTimeColumns) {
                validateDateTime(sheet, row, columns, columnName, result, false);
            }
        }
    }

    void validateDiagnosisSelections(Row row, Map<String, Integer> columns, TemplateValidationResult result) {
        validateDiagnosisSelections(new ExcelRow(row), columns, result);
    }

    private void validateDiagnosisSelections(RowView row, Map<String, Integer> columns, TemplateValidationResult result) {
        Map<String, Coding> systems =
                DiagnosisValues.systems();
        for (String[] pair : List.of(new String[] {"Code", "Codesystem"},
                new String[] {"Zusatzcode", "Zusatzcodesystem"})) {
            String code = get(row, columns, pair[0]);
            String selection = get(row, columns, pair[1]);
            if (isBlank(code) && isBlank(selection)) {
                continue;
            }
            var coding = systems.get(selection);
            if (!isBlank(selection) && coding == null && !DiagnosisValues.isSystemUri(selection)) {
                add(result, ERROR, "Diagnose", row.getRowNum() + 1, pair[1], "Codesystem requires a suggested name or an absolute URI");
            }
            try {
                DiagnosisValues.absentReason(code);
            } catch (Exception e) {
                add(result, ERROR, "Diagnose", row.getRowNum() + 1, pair[0], "Unknown explicit data absent reason");
            }
        }
        Map<String, Map<String, String>> statuses = Map.of(
                "Klinischer Status", DiagnosisValues.CLINICAL,
                "Verifikationsstatus", DiagnosisValues.VERIFICATION);
        statuses.forEach((column, values) -> {
            String value = get(row, columns, column);
            if (!isBlank(value) && !values.containsKey(value)) {
                try {
                    DiagnosisValues.absentReason(value);
                } catch (Exception e) {
                    add(result, ERROR, "Diagnose", row.getRowNum() + 1, column, "Unsupported status or data absent reason");
                }
            }
        });
    }

    private DateTimeType validateDateTime(TableView sheet, RowView row, Map<String, Integer> columns, String columnName,
            TemplateValidationResult result, boolean required) {
        CellView cell = getCell(row, columns, columnName);
        String value = get(row, columns, columnName);
        if (isBlank(value)) {
            if (required) {
                add(result, ERROR, sheet.getSheetName(), row.getRowNum() + 1, columnName, "Value is required");
            }
            return null;
        }
        if (cell != null && cell.hasDate()) {
            return new DateTimeType(cell.date());
        }
        try {
            if (DiagnosisValues.absentReason(value) != null) {
                return null;
            }
            if (value.matches("\\d{4}(-\\d{2}(-\\d{2})?)?(T.*)?")) {
                return new DateTimeType(value);
            }
            return de.uni_leipzig.life.synthifhir.utils.DateUtil.parseDateTimeType(value);
        } catch (Exception e) {
            add(result, ERROR, sheet.getSheetName(), row.getRowNum() + 1, columnName,
                    "Value is not a supported date/time: " + value);
            return null;
        }
    }

    private DateTimeType validateDateValue(TableView sheet, RowView row, Map<String, Integer> columns, String columnName,
            TemplateValidationResult result, boolean required) {
        CellView cell = getCell(row, columns, columnName);
        String value = get(row, columns, columnName);
        if (isBlank(value)) {
            if (required) {
                add(result, ERROR, sheet.getSheetName(), row.getRowNum() + 1, columnName, "Value is required");
            }
            return null;
        }
        if ("Person".equals(sheet.getSheetName()) && "Geburtsdatum".equals(columnName) && DiagnosisValues.isAbsent(value)) {
            try {
                return (DateTimeType)new DateTimeType().addExtension(DiagnosisValues.absentReason(value));
            } catch (IllegalArgumentException e) {
                add(result, ERROR, sheet.getSheetName(), row.getRowNum() + 1, columnName, e.getMessage());
                return null;
            }
        }
        if (cell != null && cell.hasDate()) {
            return new DateTimeType(cell.date());
        }
        try {
            return de.uni_leipzig.life.synthifhir.utils.DateUtil.parseDateTimeType(value);
        } catch (Exception e) {
            add(result, ERROR, sheet.getSheetName(), row.getRowNum() + 1, columnName,
                    "Value is not a supported date/time: " + value);
            return null;
        }
    }

    private void validateRequired(TableView sheet, RowView row, Map<String, Integer> columns, String columnName,
            TemplateValidationResult result) {
        if (isBlank(get(row, columns, columnName))) {
            add(result, ERROR, sheet.getSheetName(), row.getRowNum() + 1, columnName,
                    columnName + " is required when input consistency checks are enabled");
        }
    }

    private List<String> readHeaders(TableView sheet) {
        RowView headerRow = sheet.getRow(0);
        List<String> headers = new ArrayList<>();
        if (headerRow == null) {
            return headers;
        }
        for (CellView cell : headerRow) {
            headers.add(cell.text().trim());
        }
        for (int i = headers.size() - 1; i >= 0 && headers.get(i).isBlank(); i--) {
            headers.remove(i);
        }
        return headers;
    }

    private Map<String, Integer> columnIndexes(TableView sheet) {
        Map<String, Integer> columns = new HashMap<>();
        RowView headerRow = sheet.getRow(0);
        if (headerRow == null) {
            return columns;
        }
        for (CellView cell : headerRow) {
            String header = cell.text().trim();
            if (!header.isBlank()) {
                columns.put(header, cell.getColumnIndex());
            }
        }
        return columns;
    }

    private boolean isEmptyDataRow(RowView row, Map<String, Integer> columns) {
        if (row == null) {
            return true;
        }
        for (Map.Entry<String, Integer> column : columns.entrySet()) {
            if ("Erklärung/Ausfüllhilfe".equals(column.getKey())) {
                continue;
            }
            CellView cell = row.getCell(column.getValue());
            if (cell != null && !isBlank(cell.text())) {
                return false;
            }
        }
        return true;
    }

    private String get(RowView row, Map<String, Integer> columns, String columnName) {
        CellView cell = getCell(row, columns, columnName);
        return cell == null ? "" : cell.text().trim();
    }

    private CellView getCell(RowView row, Map<String, Integer> columns, String columnName) {
        Integer columnIndex = columns.get(columnName);
        if (row == null || columnIndex == null) {
            return null;
        }
        return row.getCell(columnIndex);
    }

    private List<String> getEncounterNumbers(RowView row, Map<String, Integer> columns) {
        String value = get(row, columns, "Fall-Nr");
        if (isBlank(value)) {
            return List.of();
        }
        List<String> encounterNumbers = new ArrayList<>();
        for (String encounterNumber : value.split(",")) {
            String trimmedEncounterNumber = encounterNumber.trim();
            if (!trimmedEncounterNumber.isEmpty()) {
                encounterNumbers.add(trimmedEncounterNumber);
            }
        }
        return encounterNumbers;
    }

    private boolean isExcelDateCell(Cell cell) {
        return cell != null && cell.getCellType() == CellType.NUMERIC && DateUtil.isCellDateFormatted(cell);
    }

    private boolean isExcelDateFormulaCell(Cell cell) {
        return cell != null && cell.getCellType() == CellType.FORMULA && DateUtil.isCellDateFormatted(cell);
    }

    private Date evaluateDateFormula(Cell cell) {
        CellValue cellValue = formulaEvaluator.evaluate(cell);
        return DateUtil.getJavaDate(cellValue.getNumberValue());
    }

    private String formatCell(Cell cell) {
        if (cell instanceof org.apache.poi.xssf.usermodel.XSSFCell
                && cell.getCellType() == CellType.FORMULA) {
            // DataFormatter evaluates into the cell cache. CSV export must retain the
            // source cache, just as it did when it opened a separate workbook.
            var formula = (org.apache.poi.xssf.usermodel.XSSFCell) cell;
            var xml = formula.getCTCell();
            boolean hadValue = xml.isSetV(), hadType = xml.isSetT();
            String value = xml.getV();
            var type = xml.getT();
            try {
                return formatter.formatCellValue(cell, formulaEvaluator);
            } finally {
                if (hadValue) xml.setV(value); else xml.unsetV();
                if (hadType) xml.setT(type); else xml.unsetT();
            }
        }
        return formatter.formatCellValue(cell, formulaEvaluator);
    }

    private interface CellView {
        int getColumnIndex();
        String text();
        default boolean hasDate() { return false; }
        default Date date() { throw new IllegalStateException("Text cell has no Excel date"); }
    }
    private interface RowView extends Iterable<CellView> {
        int getRowNum();
        CellView getCell(int index);
    }
    private interface TableView extends Iterable<RowView> {
        String getSheetName();
        RowView getRow(int index);
    }
    private static final class TextCell implements CellView {
        private final int index;
        private final String value;
        TextCell(int index, String value) { this.index = index; this.value = value; }
        @Override
        public int getColumnIndex() { return index; }
        @Override
        public String text() { return value; }
    }
    private static final class TextRow implements RowView {
        private final int index;
        private final List<CellView> cells;
        TextRow(int index, List<CellView> cells) { this.index = index; this.cells = cells; }
        @Override
        public int getRowNum() { return index; }
        @Override
        public CellView getCell(int index) { return index < cells.size() ? cells.get(index) : null; }
        @Override
        public java.util.Iterator<CellView> iterator() { return cells.iterator(); }
    }
    private static final class TextTable implements TableView {
        private final String name;
        private final List<RowView> rows;
        TextTable(String name, List<RowView> rows) { this.name = name; this.rows = rows; }
        @Override
        public String getSheetName() { return name; }
        @Override
        public RowView getRow(int index) { return index < rows.size() ? rows.get(index) : null; }
        @Override
        public java.util.Iterator<RowView> iterator() { return rows.iterator(); }
    }
    private final class ExcelCell implements CellView {
        private final Cell cell;
        ExcelCell(Cell cell) { this.cell = cell; }
        @Override
        public int getColumnIndex() { return cell.getColumnIndex(); }
        @Override
        public String text() { return formatCell(cell); }
        @Override
        public boolean hasDate() { return isExcelDateCell(cell) || isExcelDateFormulaCell(cell); }
        @Override
        public Date date() { return isExcelDateCell(cell) ? cell.getDateCellValue() : evaluateDateFormula(cell); }
    }
    private final class ExcelRow implements RowView {
        private final Row row;
        ExcelRow(Row row) { this.row = row; }
        @Override
        public int getRowNum() { return row.getRowNum(); }
        @Override
        public CellView getCell(int index) { var cell = row.getCell(index); return cell == null ? null : new ExcelCell(cell); }
        @Override
        public java.util.Iterator<CellView> iterator() {
            return java.util.stream.StreamSupport.stream(row.spliterator(), false)
                    .<CellView>map(ExcelCell::new).iterator();
        }
    }
    private final class ExcelTable implements TableView {
        private final XSSFSheet sheet;
        ExcelTable(XSSFSheet sheet) { this.sheet = sheet; }
        @Override
        public String getSheetName() { return sheet.getSheetName(); }
        @Override
        public RowView getRow(int index) { var row = sheet.getRow(index); return row == null ? null : new ExcelRow(row); }
        @Override
        public java.util.Iterator<RowView> iterator() {
            return java.util.stream.StreamSupport.stream(sheet.spliterator(), false)
                    .<RowView>map(ExcelRow::new).iterator();
        }
    }

    private void add(TemplateValidationResult result, Severity severity, String sheetName, int rowNumber,
            String columnName, String message) {
        result.add(new TemplateValidationIssue(severity, sheetName, rowNumber, columnName, message));
    }

    private void log(TemplateValidationResult result) {
        for (TemplateValidationIssue issue : result.getIssues()) {
            if (issue.isError()) {
                LOG.error(issue.toString());
            } else {
                LOG.warn(issue.toString());
            }
        }
        LOG.info("Excel template validation found {} error(s) and {} warning(s)", result.getErrorCount(),
                result.getWarningCount());
    }

    private boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private static Map<String, List<String>> createExpectedHeaders() {
        Map<String, List<String>> headers = new LinkedHashMap<>();
        headers.put("Person", Arrays.asList("Patient-ID", "Vorname", "Nachname", "Geburtsdatum",
                "Geschlecht", "Datum Einwilligung", "PDAT Einwilligung",
                "KKDAT retro Einwilligung", "KKDAT Einwilligung", "BIOMAT Einwilligung",
                "BIOMAT Zusatz Einwilligung", "Straße", "Postleitzahl", "Ort", "Bundesland", "Land", "Sterbezeitpunkt", "Erklärung/Ausfüllhilfe"));
        headers.put("Fall", Arrays.asList("Patient-ID", "Fall-Nr", "Start", "Ende", "Einrichtungskontaktklasse",
                "Fachabteilung", "Station", "Zimmer", "Bett", AdmissionReasonValues.COLUMN,
                "Kontaktart", "Erklärung/Ausfüllhilfe"));
        headers.put("Laborbefund", Arrays.asList("Patient-ID", "Fall-Nr", "LOINC", "Codesystem", "Zusatzcode", "Zusatzcodesystem", "Parameter", "Messwert",
                "Einheit", "Zeitstempel (Abnahme)", "Werttyp", "Wertcode", "Wertcodesystem", "Kategorie", "Status", "Untersuchung ID", "Komponente von", "Ausgabezeitpunkt", "Einheitencode", "Einheitencodesystem", "Erklärung/Ausfüllhilfe"));
        headers.put("Diagnose", Arrays.asList("Patient-ID", "Fall-Nr", "Bezeichner", "Code", "Codesystem",
                "Zusatzcode", "Zusatzcodesystem", "Dokumentationszeitpunkt", "Beginn", "Ende",
                "Klinischer Status", "Verifikationsstatus", "Typ", "Version", "Zusatzversion", "Erklärung/Ausfüllhilfe"));
        headers.put("Prozedur", Arrays.asList("Patient-ID", "Fall-Nr", "Prozedurentext", "Prozedurencode",
                "Durchführungsbeginn", "Codesystem", "Zusatzcode", "Zusatzcodesystem", "Ende", "Status", "Kategorie", "Version", "Zusatzversion", "Erklärung/Ausfüllhilfe"));
        headers.put("Medikation", Arrays.asList("Patient-ID", "Fall-Nr", "Medikationstyp", "Präparatbezeichnung", "Präparatcode", "Präparatcodesystem", "ATC-Code", "ATC-Version", "Darreichungsform", "Wirkstoffcode", "Wirkstoffcodesystem", "Status", "Absicht", "Dokumentationszeitpunkt", "Beginn", "Ende", "Einzeldosis", "Dosiereinheit", "Dosiereinheitcodesystem", "Dosen pro Tag", "Dosierungstext", "Erklärung/Ausfüllhilfe"));
        headers.put("Klinische Dokumentation", Arrays.asList("Patient-ID", "Fall-Nr", "Bezeichner", "Untersuchungscode", "Codesystem", "Zusatzcode", "Zusatzcodesystem", "Wert",
                "Einheit", "Zeitstempel", "Werttyp", "Wertcode", "Wertcodesystem", "Kategorie", "Status", "Untersuchung ID", "Komponente von", "Ausgabezeitpunkt", "Einheitencode", "Einheitencodesystem", "Erklärung/Ausfüllhilfe"));
        headers.put("DocumentReference", Arrays.asList("Dokument-ID", "Patient-ID", "Fall-Nr", "Dateipfad", "Embed",
                "Dokumenttext", "Status", "Ausgabezeitpunkt", "Dokumentcode", "Dokumentcodesystem", "Dokumentbezeichner", "NLPStatus", "Erklärung/Ausfüllhilfe"));
        headers.put("Impfung", Arrays.asList("Patient-ID", "Fall-Nr", "Eintrag ID", "Bezeichner", "Code", "Codesystem", "Zeitpunkt", "Status", "Primärquelle", "Version", "Erklärung/Ausfüllhilfe"));
        headers.put("Befundbericht", Arrays.asList("Patient-ID", "Fall-Nr", "Eintrag ID", "Bezeichner", "Code", "Codesystem", "Zeitpunkt", "Status", "Ausgabezeitpunkt", "Ergebnisse", "Beschreibung", "Erklärung/Ausfüllhilfe"));
        headers.put("Behandlungsplan", Arrays.asList("Patient-ID", "Fall-Nr", "Titel", "Beschreibung", "Planstatus", "Absicht", "Erstellungsdatum", "Beginn", "Ende", "Kategorietext", "Kategoriecode", "Kategoriecodesystem", "Kategorieversion", "Aktivitätstext", "Aktivitätscode", "Aktivitätscodesystem", "Aktivitätsversion", "Aktivitätsstatus", "Aktivitätsstatusgrund", "Aktivitätsbeginn", "Aktivitätsende", "Aktivitätsort", "Erklärung/Ausfüllhilfe"));
        return headers;
    }

}
