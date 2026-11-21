package de.uni_leipzig.imise;

import static de.uni_leipzig.imise.utils.FileTools.ensureEmptyDirectory;

import java.io.File;
import java.io.FilenameFilter;
import java.io.IOException;
import java.util.Collection;
import java.util.List;
import java.nio.file.Path;
import java.nio.file.Files;
import de.uni_leipzig.life.synthifhir.ConverterOptionSet;
import de.uni_leipzig.imise.validate.TemplateValidationException;

import org.apache.commons.io.FilenameUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import de.uni_leipzig.UcumMapper;
import de.uni_leipzig.imise.utils.Excel2Csv;
import de.uni_leipzig.imise.validate.ExcelTemplateValidator;
import de.uni_leipzig.imise.validate.FHIRValidator;
import de.uni_leipzig.imise.validate.FHIRValidator.ValidationResultType;
import de.uni_leipzig.life.synthifhir.ConverterResult.ConverterResultStatistics;
import de.uni_leipzig.life.synthifhir.Csv2Fhir;
import de.uni_leipzig.life.synthifhir.AdditionalIdentifiers;
import de.uni_leipzig.life.synthifhir.OutputFileType;

/**
 * @author fmeineke (02.11.2020), AXS (21.11.2021)
 */
public class Excel2Fhir {

    /**  */
    private static Logger LOG = LoggerFactory.getLogger(Excel2Fhir.class);

    /**  */
    private final ExcelTemplateValidator templateValidator = new ExcelTemplateValidator();

    /**  */
    private FHIRValidator validator;
    private final boolean validateOutput;
    private boolean validationRequested, validationProblems;

    public boolean wasValidationRequested() { return validationRequested; }
    private final ValidationResultType minLogLevel;

    IdentifierSequence identifierSequence;

    private boolean importProblems;
    private final List<File> optionFiles;
    private final Path optionsDirectory;

    public boolean hasImportProblems() { return importProblems; }

    public boolean hasValidationProblems() {
        return validationProblems;
    }

    /** Counters for all created resources */
    private final ConverterResultStatistics allFilesStatistics = new ConverterResultStatistics();

    /**
     * @param excelFile
     * @return
     */
    private static File getTargetCSVDir(File excelFile) {
        String path = excelFile.getPath();
        path = FilenameUtils.removeExtension(path);
        File targetCSVDir = new File(path, "output");
        return targetCSVDir;
    }

    /**
     * @param validate
     * @param minLogLevel
     */
    public Excel2Fhir(boolean validate, ValidationResultType minLogLevel) {
        this(validate, minLogLevel, List.of(), null);
    }

    public Excel2Fhir(boolean validate, ValidationResultType minLogLevel, List<File> optionFiles, Path optionsDirectory) {
        this.optionFiles = List.copyOf(optionFiles);
        this.optionsDirectory = optionsDirectory;
        this.validateOutput = validate;
        this.minLogLevel = minLogLevel;
    }

    /**
     * @param sourceExcelFileOrDirectory
     * @param targetCSVDir
     * @param targetJSONDir
     * @throws IOException
     */
    private static void createAndCleanOutputDirectories(File sourceExcelFileOrDirectory, File targetCSVDir,
            File targetJSONDir)
            throws IOException {
        File absoluteSourceExcelFileOrDirectory = sourceExcelFileOrDirectory.getAbsoluteFile();
        File sourceExcelDir = absoluteSourceExcelFileOrDirectory.isDirectory() ? absoluteSourceExcelFileOrDirectory
                : absoluteSourceExcelFileOrDirectory.getParentFile();
        // create and reset directories
        if (targetCSVDir == null) {
            targetCSVDir = getTargetCSVDir(sourceExcelDir);
        }
        ensureEmptyDirectory(targetCSVDir, sourceExcelDir);
        if (targetJSONDir == null) {
            targetJSONDir = targetCSVDir;
        }
        if (targetJSONDir != targetCSVDir) {
            ensureEmptyDirectory(targetJSONDir, sourceExcelDir);
        }
    }

    /**
     * @param sourceExcelDir
     * @param sheetNamePatterns if not <code>null</code> then only the sheets with
     *                          a name in this collection will be convertert to
     *                          csv. If <code>null</code> then all sheet will be
     *                          convertet.
     * @throws IOException
     */
    public void convertAllExcelInDir(File sourceExcelDir, Collection<String> sheetNamePatterns) throws IOException {
        convertAllExcelInDir(sourceExcelDir, sheetNamePatterns, null, null, Integer.MAX_VALUE);
    }

    /**
     * @param sourceExcelDir
     * @param sheetNamePatterns if not <code>null</code> then only the sheets with
     *                          a name in this collection will be convertert to
     *                          csv. If <code>null</code> then all sheet will be
     *                          convertet.
     * @param tempDir
     * @param resultDir
     * @param patientsPerBundle
     * @param outputFileTypes
     * @throws IOException
     */
    public void convertAllExcelInDir(File sourceExcelDir, Collection<String> sheetNamePatterns, File tempDir,
            File resultDir, int patientsPerBundle, OutputFileType... outputFileTypes)
            throws IOException {
        FilenameFilter filter = (dir, name) -> !name.startsWith("~") && name.toLowerCase().endsWith(".xlsx");
        createAndCleanOutputDirectories(sourceExcelDir, tempDir, resultDir);
        File[] sources = sourceExcelDir.listFiles(filter);
        java.util.Arrays.sort(sources);
        var identifiers = new java.util.HashMap<String, AdditionalIdentifiers>();
        for (File sourceExcelFile : sources) {
            File csv = sources.length == 1 ? tempDir : new File(tempDir, sourceExcelFile.getName());
            Files.createDirectories(csv.toPath());
            convertExcelFile(sourceExcelFile, sheetNamePatterns, csv, resultDir, patientsPerBundle, false,
                    sources.length > 1 ? sourceExcelFile.getName() : null, identifiers, outputFileTypes);
        }
    }

    /**
     * @param sourceExcelFile
     * @param sheetNamePatterns if not <code>null</code> then only the sheets with
     *                          a name in this collection will be convertert to
     *                          csv. If <code>null</code> then all sheet will be
     *                          convertet.
     * @param tempDir
     * @param resultDir
     * @param patientsPerBundle
     * @param outputFileTypes
     * @throws IOException
     */
    public void convertExcelFile(File sourceExcelFile, Collection<String> sheetNamePatterns, File tempDir,
            File resultDir, int patientsPerBundle, OutputFileType... outputFileTypes)
            throws IOException {
        convertExcelFile(sourceExcelFile, sheetNamePatterns, tempDir, resultDir, patientsPerBundle, true, null,
                new java.util.HashMap<>(), outputFileTypes);
    }

    /**
     * @param sourceExcelFile
     * @param sheetNamePatterns               if not <code>null</code> then only
     *                                        the sheets with a name in this
     *                                        collection will be convertert to csv.
     *                                        If <code>null</code> then all sheet
     *                                        will be convertet.
     * @param tempDir
     * @param resultDir
     * @param outputFileTypes
     * @param patientsPerBundle
     * @param createAndCleanOutputDirectories
     * @throws IOException
     */
    private void convertExcelFile(File sourceExcelFile, Collection<String> sheetNamePatterns, File tempDir,
            File resultDir,
            int patientsPerBundle, boolean createAndCleanOutputDirectories, String inputName,
            java.util.Map<String, AdditionalIdentifiers> identifiers, OutputFileType... outputFileTypes)
            throws IOException {
        var sets = optionFiles.isEmpty() ? ConverterOptionSet.defaults()
                : ConverterOptionSet.external(optionFiles);
        try (var workbook = de.uni_leipzig.imise.utils.ExcelWorkbooks.openReadOnly(sourceExcelFile)) {
            for (var set : sets) {
                var checked = templateValidator.validate(workbook, set.options(), set.name());
                if (checked.hasErrors()) throw new TemplateValidationException(checked);
            }
            if (createAndCleanOutputDirectories) {
                createAndCleanOutputDirectories(sourceExcelFile, tempDir, resultDir);
            }
            Excel2Csv.splitExcel(workbook, sourceExcelFile, sheetNamePatterns, tempDir);
        }
        convertTables(sourceExcelFile, tempDir, resultDir, patientsPerBundle, inputName, identifiers, sets, outputFileTypes);
    }

    /** Convert internally generated CSV tables with the same validation and output policy as Excel. */
    public void convertCsvDirectory(File source, File input, File resultDir,
            int patientsPerBundle, OutputFileType... outputFileTypes) throws IOException {
        var sets = optionFiles.isEmpty() ? ConverterOptionSet.defaults() : ConverterOptionSet.external(optionFiles);
        String prefix = FilenameUtils.removeExtension(source.getName()) + "_";
        for (var set : sets) {
            var checked = templateValidator.validateCsv(input.toPath(), prefix, set.options(), set.name());
            if (checked.hasErrors()) throw new TemplateValidationException(checked);
        }
        convertTables(source, input, resultDir, patientsPerBundle, null, new java.util.HashMap<>(), sets, outputFileTypes);
    }

    private void convertTables(File sourceExcelFile, File tempDir, File resultDir, int patientsPerBundle,
            String inputName, java.util.Map<String, AdditionalIdentifiers> identifiers,
            List<ConverterOptionSet> sets, OutputFileType... outputFileTypes) throws IOException {
        if (validator == null && sets.stream().anyMatch(set -> set.options().validationEnabled(validateOutput)))
            validator = new FHIRValidator(minLogLevel);
        String fileBaseName = FilenameUtils.removeExtension(sourceExcelFile.getName()) + "-";
        for (var set : sets) {
            Path destination = resultDir.toPath();
            if (sets.size() > 1) destination = destination.resolve(set.directoryName());
            if (inputName != null) destination = destination.resolve(inputName);
            Files.createDirectories(destination);
            Path snapshots = optionsDirectory == null ? resultDir.toPath().resolve("options") : optionsDirectory;
            set.snapshot(snapshots.resolve(sourceExcelFile.getName()).resolve(set.directoryName()), patientsPerBundle, validateOutput, outputFileTypes);
            var additional = identifiers.computeIfAbsent(set.name(), key -> identifierSequence == null
                    ? new AdditionalIdentifiers(set.options().configuration())
                    : identifierSequence.create(set.options().configuration()));
            Csv2Fhir converter = new Csv2Fhir(tempDir, destination.toFile(), fileBaseName, validator, set.options(), additional);
            try {
                ConverterResultStatistics converterStatistics = converter.convertFiles(patientsPerBundle, outputFileTypes);
                allFilesStatistics.add(converterStatistics);
                if (identifierSequence != null) identifierSequence.completed(additional);
            } catch (Exception e) {
                throw new IOException("FHIR conversion failed for " + sourceExcelFile, e);
            } finally {
                importProblems |= converter.hasImportProblems();
                validationRequested |= converter.wasValidationRequested();
                validationProblems |= converter.hasValidationProblems();
            }
        }
        if (!UcumMapper.invalidUcumCodes.isEmpty()) {
            LOG.error("Invalid UCUM codes in all files at this point " + UcumMapper.invalidUcumCodes);
        }
        LOG.info("All bundles of all files content:\n" + allFilesStatistics);
    }

}
