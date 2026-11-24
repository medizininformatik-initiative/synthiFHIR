package de.uni_leipzig.imise;

import java.io.File;
import java.util.List;
import java.util.concurrent.Callable;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.google.common.base.Stopwatch;

import de.uni_leipzig.imise.utils.FileLogger;
import de.uni_leipzig.imise.utils.FileLogger.LogContentLayout;
import de.uni_leipzig.imise.utils.WorkflowRun;
import de.uni_leipzig.imise.validate.FHIRValidator.ValidationResultType;
import de.uni_leipzig.life.synthifhir.OutputFileType;
import de.uni_leipzig.life.synthifhir.PrintExceptionMessageHandler;
import de.uni_leipzig.life.synthifhir.TableIdentifier;
import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

/**
 * @author AXS (14.11.2021)
 */
@Command(name = "synthifhir", mixinStandardHelpOptions = true, version = "1.0", description = "Converts a directory containing multiple excel files into (a) json bundle(s).")
public class Excel2FhirMain implements Callable<Integer> {

    private final File csvInput;
    IdentifierSequence identifierSequence;

    public Excel2FhirMain() { this(null); }

    Excel2FhirMain(File csvInput) { this.csvInput = csvInput; }

    /**  */
    private static Logger LOG = LoggerFactory.getLogger(Excel2FhirMain.class);

    @Option(names = { "-f",
            "--input-file" }, paramLabel = "INPUT-File", description = "Input Excel file; use either -f or -i.")
    File inputFile;

    @Option(names = { "-i",
            "--input-directory" }, paramLabel = "INPUT-DIRECTORY", description = "Input directory for Excel files. Default: input in the working directory.")
    File inputDirectory;

    @Option(names = { "-o",
            "--output-directory" }, paramLabel = "OUTPUT-DIRECTORY", description = "Output root for fresh runs. Default: outputGlobal in the working directory.")
    File outputDirectory;

    @Option(names = { "-t",
            "--temp-directory" }, paramLabel = "TEMP-DIRECTORY", description = "Optional CSV root; creates a fresh run subdirectory. Default: details/csv inside the output run.")
    File tempDirectory;

    @Option(names = "--export-default-options", paramLabel = "FILE", description = "Write the default configuration and exit.")
    File exportDefaultOptions;

    @Option(names = "--converter-options", paramLabel = "FILE", description = "External converter configuration (one per run).")
    List<File> converterOptions = new java.util.ArrayList<>();

    @Option(names = { "-r",
            "--result-file-format" }, split = ",", paramLabel = "RESULT-FILE-FORMAT", description = "Output formats (comma separated). Default: JSON,NDJSON. Also XML,JSONGZIP,JSONBZ2,ZIPJSON.")
    OutputFileType[] outputFileTypes = { OutputFileType.JSON, OutputFileType.NDJSON };

    @Option(names = { "-p",
            "--patients-count" }, paramLabel = "PATIENTS-COUNT", description = "Maximum number of patients in one file (default: 1).")
    int patientsPerBundle = 1;

    @Option(names = { "-l",
            "--log-layout" }, paramLabel = "LOG-FILE-LAYOUT", description = "The layout of the log content in the logfile.")
    LogContentLayout logFileContentLayout = LogContentLayout.DATE_LEVEL_SOURCE_LINENUMBER; // the console log
                                                                                           // layout is set in
                                                                                           // the projects
                                                                                           // log4j2.xml file!

    @Option(names = { "-v",
            "--validate-bundles" }, negatable = true, defaultValue = "false", fallbackValue = "true", paramLabel = "VALIDATE-BUNDLES", description = "Enables FHIR bundle validation (default: disabled), writes reports and exits nonzero on errors or incomplete checks.")
    boolean validateBundles = false;

    @Option(names = { "-vll",
            "--validation-log-level" }, paramLabel = "VALIDATION-LOG-LEVEL", description = "Sets the log level for validation. Default ist ERROR. Other values are IGNORED, WARNING or VALID")
    ValidationResultType minLogLevel = ValidationResultType.ERROR;

    /**
     * @param args
     */
    public static void main(String[] args) {
        LOG.info("Started...");
        Stopwatch stopwatch = Stopwatch.createStarted();

        Excel2FhirMain excel2Fhir = new Excel2FhirMain();
        CommandLine cmd = new CommandLine(excel2Fhir).setExecutionExceptionHandler(new PrintExceptionMessageHandler());

        int exitCode = cmd.execute(args);

        LOG.info("Finished in " + stopwatch.stop());

        System.exit(exitCode);
    }

    @Override
    public Integer call() throws Exception {
        if (converterOptions.size() > 1) throw new IllegalArgumentException("Choose one converter configuration per run");
        if (exportDefaultOptions != null) {
            java.nio.file.Files.writeString(exportDefaultOptions.toPath(),
                    de.uni_leipzig.life.synthifhir.ContractConfiguration.defaultProperties(),
                    java.nio.file.StandardOpenOption.CREATE_NEW);
            return 0;
        }
        if (patientsPerBundle < 1)
            throw new IllegalArgumentException("-p must be positive.");
        if (inputFile != null && inputDirectory != null) {
            throw new IllegalArgumentException("Choose either -f or -i.");
        }
        if (inputFile == null && inputDirectory == null)
            inputDirectory = new File("input");
        if (csvInput == null && inputFile != null && !inputFile.isFile())
            throw new IllegalArgumentException("Input file not found: " + inputFile);
        if (inputDirectory != null) {
            File[] inputs = inputDirectory.listFiles(f -> f.isFile() && !f.getName().startsWith("~")
                    && f.getName().toLowerCase(java.util.Locale.ROOT).endsWith(".xlsx"));
            if (inputs == null || inputs.length == 0)
                throw new IllegalArgumentException("No Excel workbooks in " + inputDirectory);
        }
        if (csvInput != null && (!csvInput.isDirectory() || inputFile == null || inputDirectory != null))
            throw new IllegalArgumentException("CSV batch input requires a directory and a logical file name");
        WorkflowRun run = new WorkflowRun(outputDirectory, tempDirectory, csvInput == null ? "excel-to-fhir" : "csv-to-fhir");
        try (var log = FileLogger.openRootFileLogger(run.directory.resolve("details/logs/conversion.log").toFile(),
                logFileContentLayout)) {
            List<String> sheets = TableIdentifier.getExcelSheetNamePatterns();
            Excel2Fhir converter = new Excel2Fhir(validateBundles, minLogLevel, converterOptions, run.directory.resolve("details/options"));
            converter.identifierSequence = identifierSequence;
            if (csvInput != null) {
                converter.convertCsvDirectory(inputFile, csvInput, run.staging.toFile(),
                        patientsPerBundle, outputFileTypes);
            } else if (inputFile != null) {
                converter.convertExcelFile(inputFile, sheets, run.csv.toFile(), run.staging.toFile(),
                        patientsPerBundle, outputFileTypes);
            } else {
                converter.convertAllExcelInDir(inputDirectory, sheets, run.csv.toFile(), run.staging.toFile(),
                        patientsPerBundle, outputFileTypes);
            }
            return run.finish(converter.hasImportProblems(), converter.hasValidationProblems(), converter.wasValidationRequested());
        } catch (Exception e) {
            run.fail(e);
            LOG.error(e.getMessage(), e);
            return 1;
        }
    }
}
