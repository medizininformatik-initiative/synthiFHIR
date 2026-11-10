package de.uni_leipzig.life.synthifhir;

import static de.uni_leipzig.life.synthifhir.BundleFunctions.getBaseId;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

import org.hl7.fhir.r4.model.Condition;
import org.hl7.fhir.r4.model.Encounter;
import org.hl7.fhir.r4.model.Medication;
import org.hl7.fhir.r4.model.MedicationAdministration;
import org.hl7.fhir.r4.model.MedicationStatement;
import org.hl7.fhir.r4.model.Observation;
import org.hl7.fhir.r4.model.Person;
import org.hl7.fhir.r4.model.Procedure;
import org.hl7.fhir.r4.model.Resource;

import com.google.common.base.Strings;
import com.google.common.collect.ArrayListMultimap;
import com.google.common.collect.HashMultimap;
import com.google.common.collect.Multimap;

import de.uni_leipzig.imise.utils.Alphabetical;
import de.uni_leipzig.life.synthifhir.ConverterOptions.IntOption;
import de.uni_leipzig.life.synthifhir.converter.ContactConversionState;

/**
 * @author AXS (29.11.2021)
 */
public class ConverterResult {
    public org.hl7.fhir.r4.model.CarePlan currentCarePlan;
    public org.hl7.fhir.r4.model.DocumentReference currentDocumentReference;
    public final List<Map<String, String>> contactEndDerivations = new ArrayList<>();
    private final ContactConversionState contactState =
            new ContactConversionState();
    private final ContactIndex contacts = new ContactIndex();

    public ContactConversionState contactState() { return contactState; }
    public ContactIndex contacts() { return contacts; }

    public static final class InputContext {
        private final String patientId;
        private final List<String> encounterIds;
        private final long inputRow;
        private final boolean explicitDocumentTimestamp;
        public InputContext(String patientId, List<String> encounterIds, long inputRow, boolean explicitDocumentTimestamp) {
            this.patientId = patientId;
            this.encounterIds = List.copyOf(encounterIds);
            this.inputRow = inputRow;
            this.explicitDocumentTimestamp = explicitDocumentTimestamp;
        }
        public String patientId() { return patientId; }
        public List<String> encounterIds() { return encounterIds; }
        public long inputRow() { return inputRow; }
        public boolean explicitDocumentTimestamp() { return explicitDocumentTimestamp; }
    }
    private final Map<String, InputContext> inputContexts = new HashMap<>();
    public void recordInput(Resource resource, InputContext context) {
        inputContexts.put(resource.fhirType() + "/" + resource.getIdElement().getIdPart(), context);
    }
    public InputContext inputContext(Resource resource) {
        return inputContexts.get(resource.fhirType() + "/" + resource.getIdElement().getIdPart());
    }

    /**
     * The options which contains the idStart values.
     */
    private final ConverterOptions converterOptions;

    /**
     * Maps from the Resource type and the id to the resource created by this
     * converter.<br>
     * Must be a {@link Multimap} because from the
     * {@link TableIdentifier#Medikation} we generate 3 different types of
     * resources. <br>
     * <br>
     * A valid convertion result with all resource types has the structure:<br>
     * {@link TableIdentifier#Person} ->
     * {{@link ConvertedResources}<{@link Person}>},<br>
     * {@link TableIdentifier#Versorgungsfall} ->
     * {{@link ConvertedResources}<{@link Encounter}>},<br>
     * {@link TableIdentifier#Abteilungsfall} ->
     * {{@link ConvertedResources}<{@link Encounter}>},<br>
     * {@link TableIdentifier#Diagnose} ->
     * {{@link ConvertedResources}<{@link Condition}>},<br>
     * {@link TableIdentifier#Prozedur} ->
     * {{@link ConvertedResources}<{@link Procedure}>},<br>
     * {@link TableIdentifier#Klinische_Dokumentation} ->
     * {{@link ConvertedResources}<{@link Observation}>},<br>
     * {@link TableIdentifier#Laborbefund} ->
     * {{@link ConvertedResources}<{@link Observation}>},<br>
     * {@link TableIdentifier#Medikation} ->
     * {{@link ConvertedResources}<{@link Medication}>,
     * {@link ConvertedResources}<{@link MedicationStatement}>,
     * {@link ConvertedResources}<{@link MedicationAdministration}>},<br>
     */
    private final Multimap<TableIdentifier, ConvertedResources<? extends Resource>> createdResources = ArrayListMultimap
            .create();

    /**  */
    private ConverterResultStatistics statistics = null;

    /**
     * @param converterOptions The options which contains the idStart values.
     */
    public ConverterResult(ConverterOptions converterOptions) {
        this.converterOptions = converterOptions;
    }

    /**
     * @return
     */
    public ConverterOptions getConverterOptions() {
        return converterOptions;
    }

    /**
     * @param tableSource
     * @param resource
     */
    @SuppressWarnings("unchecked")
    public <T extends Resource> void add(TableIdentifier tableSource, T resource) {
        statistics = null;
        Collection<ConvertedResources<? extends Resource>> typedConverterResults = createdResources.get(tableSource);
        ConvertedResources<T> typedConverterResult = null;
        Class<T> resourceClass = (Class<T>) resource.getClass();
        for (ConvertedResources<? extends Resource> result : typedConverterResults) {
            if (result.hasContentType(resourceClass)) {
                typedConverterResult = (ConvertedResources<T>) result;
            }
        }
        if (typedConverterResult == null) {
            typedConverterResult = new ConvertedResources<>(resourceClass);
            createdResources.put(tableSource, typedConverterResult);
        }
        typedConverterResult.add(resource);
    }

    /**
     * Adds all resources tor this result.
     *
     * @param resources
     * @throws ClassCastException if at least one resource in the collection has
     *                            not the generic type of this result.
     */
    public <T extends Resource> void addAll(TableIdentifier tableSource, Collection<T> resources)
            throws ClassCastException {
        for (T resource : resources) {
            add(tableSource, resource);
        }
    }

    /**
     * @param resourceType
     * @param id
     * @return
     */
    public <T extends Resource> T get(TableIdentifier tableSource, Class<T> resourceType, String id) {
        ConvertedResources<T> result = getResult(tableSource, resourceType);
        if (result != null) {
            id = getBaseId(id);
            for (T resource : result.values()) {
                String resourceID = resource.getId();
                if (Objects.equals(id, resourceID)) {
                    return resource;
                }
            }
        }
        return null;
    }

    /**
     * @param <T>
     * @param tableSource
     * @param resourceType
     * @return
     */
    @SuppressWarnings("unchecked")
    private <T extends Resource> ConvertedResources<T> getResult(TableIdentifier tableSource, Class<T> resourceType) {
        for (ConvertedResources<? extends Resource> result : createdResources.get(tableSource)) {
            if (result.hasContentType(resourceType)) {
                return (ConvertedResources<T>) result;
            }
        }
        return null;
    }

    /**
     * @param <T>
     * @param tableSource
     * @param resourceType
     * @return
     */
    @SuppressWarnings("unchecked")
    public <T extends Resource> Iterable<T> getResources(TableIdentifier tableSource, Class<T> resourceType) {
        ConvertedResources<T> result = getResult(tableSource, resourceType);
        return result != null ? result : Collections.EMPTY_LIST;
    }

    /**
     * @param tableSource
     * @param resourceType
     * @return
     */
    public <T extends Resource> int getResourceCount(TableIdentifier tableSource, Class<T> resourceType) {
        ConvertedResources<T> result = getResult(tableSource, resourceType);
        return result != null ? result.size() : 0;
    }

    /**
     * @param <T>          Subtype of the Resource
     * @param tableSource  Combination of this parameter and the resourceType
     *                     identifies the correct counter.
     * @param resourceType The resource type
     * @param startIndex   Via {@link IntOption#getValue()} the start index can be
     *                     identified. An other start index than the default 1 is
     *                     useful if we want to add e.g. additional Observations to
     *                     an existing patient who has already Observation. With
     *                     the default parameter we would create a new Observation
     *                     with the ID of an existing one which would be
     *                     overwritten on the server if we post both to the server.
     * @return
     */
    public final <T extends Resource> int getNextId(TableIdentifier tableSource, Class<T> resourceType,
            IntOption startIndex) {
        return getResourceCount(tableSource, resourceType) + converterOptions.getValue(startIndex);
    }

    /**
     * @return
     */
    public ConverterResultStatistics getStatistics() {
        if (statistics == null) {
            statistics = new ConverterResultStatistics().add(this);
        }
        return statistics;
    }

    @Override
    public String toString() {
        return getStatistics().toString();
    }

    /**
     * Can store the one count for each Resource type and output it appropriately
     * via the toString() function.
     *
     * @author AXS (07.06.2022)
     */
    public static final class ConverterResultStatistics {

        /** Maps from a resource type to the count of this type. */
        private final Map<Class<? extends Resource>, Integer> resourceCounts = new HashMap<>();

        /** Maps from a resource type to the a set of all IDs . */
        private final Multimap<Class<? extends Resource>, String> resourceIDs = HashMultimap.create();

        /**
         * Adds the values from the other statistics to this.
         *
         * @param other
         */
        public ConverterResultStatistics add(ConverterResultStatistics other) {
            for (Class<? extends Resource> resourceType : other.resourceCounts.keySet()) {
                Collection<String> ids2Add = other.resourceIDs.get(resourceType);
                add(resourceType, ids2Add);
            }
            return this;
        }

        /**
         * Adds the values from the statistics of the given {@link ConverterResult} to
         * this.
         *
         * @param result
         */
        public ConverterResultStatistics add(ConverterResult result) {
            add(result.createdResources);
            return this;
        }

        /**
         * @param createdResources
         */
        private void add(Multimap<TableIdentifier, ConvertedResources<? extends Resource>> createdResources) {
            for (TableIdentifier key : createdResources.keySet()) {
                Collection<ConvertedResources<? extends Resource>> resources = createdResources.get(key);
                for (ConvertedResources<? extends Resource> convertedResource : resources) {
                    Class<? extends Resource> resourceType = convertedResource.getContentType();
                    Set<String> ids = convertedResource.keySet();
                    add(resourceType, ids);
                }
            }
        }

        /**
         * @param resourceType
         * @param ids
         */
        private void add(Class<? extends Resource> resourceType, Collection<String> ids) {
            Integer oldCount = resourceCounts.getOrDefault(resourceType, 0);
            int value2Add = ids.size();
            resourceCounts.put(resourceType, oldCount + value2Add);
            resourceIDs.putAll(resourceType, ids);
        }

        /**
         *
         */
        public void reset() {
            resourceCounts.clear();
            resourceIDs.clear();
        }

        @Override
        public String toString() {
            return getResultTable("        ");
        }

        /**
         * @param indentation String before every table line
         * @return
         */
        private String getResultTable(String indentation) {
            List<Class<? extends Resource>> resourceTypes = new ArrayList<>(resourceCounts.keySet());
            Alphabetical.sort(resourceTypes);
            int maxNameLength = getMaxStringLength(resourceTypes) + 8; // some whitespaces after the longest string
            int maxDigitsCount = getMaxStringLength(resourceCounts.values());
            StringBuilder sb = new StringBuilder();
            int maxLineLength;
            int totalCount = 0;
            int totalUniqueCount = 0;
            for (Class<? extends Resource> resourceType : resourceTypes) {
                StringBuilder line = new StringBuilder(indentation); // some indentation
                line.append(Strings.padEnd(resourceType.getSimpleName(), maxNameLength, ' '));
                line.append(": ");
                Integer count = resourceCounts.get(resourceType);
                totalCount += count;
                line.append(Strings.padStart(count.toString(), maxDigitsCount, ' '));
                Collection<String> uniqueIDs = resourceIDs.get(resourceType);
                int uniqueCount = uniqueIDs.size();
                totalUniqueCount += uniqueCount;
                if (uniqueCount != count) {
                    line.append(" (unique : ");
                    line.append(uniqueCount);
                    line.append(")");
                }
                line.append("\n");
                maxLineLength = Math.max(maxNameLength, line.length() - 1);
                sb.append(line);
            }
            // first build the total line
            StringBuilder totalLine = new StringBuilder(indentation);
            totalLine.append(Strings.padEnd("total", maxNameLength, ' '));
            totalLine.append(": ");
            totalLine.append(Strings.padStart(Integer.toString(totalCount), maxDigitsCount, ' '));
            maxLineLength = Math.max(maxNameLength, totalLine.length()); // if there is no created resource then
                                                                         // maxLineLength is still 0 here
            if (totalUniqueCount != totalCount) {
                totalLine.append(" (unique : ");
                totalLine.append(totalUniqueCount);
                totalLine.append(")");
            }

            String delimiterLine = Strings.padEnd(indentation, maxLineLength, '-');
            sb.append(delimiterLine);
            sb.append("\n");
            sb.append(totalLine);
            return sb.toString();
        }

        /**
         * @param objectsToString
         * @return
         */
        private static int getMaxStringLength(Collection<?> objectsToString) {
            int maxLength = 0;
            int sumOfInt = 0;
            for (Object o : objectsToString) {
                String s = o instanceof Class<?> ? ((Class<?>) o).getSimpleName() : o.toString();
                int length = s.length();
                if (length > maxLength) {
                    maxLength = length;
                }
                sumOfInt += o instanceof Integer ? (Integer) o : 0;
            }
            return Math.max(maxLength, Integer.toString(sumOfInt).length());
        }

    }

}
