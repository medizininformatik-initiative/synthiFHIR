package de.uni_leipzig.imise;

import java.util.HashMap;
import java.util.Map;
import java.util.function.Consumer;
import de.uni_leipzig.life.synthifhir.AdditionalIdentifiers;
import de.uni_leipzig.life.synthifhir.ContractConfiguration;

/** Counter state shared by sequential patient conversions, with per-patient context. */
final class IdentifierSequence {
    private Map<String, Long> counters;
    private final Consumer<Map<String, String>> registrations;

    IdentifierSequence(Map<String, Long> counters, Consumer<Map<String, String>> registrations) {
        this.counters = new HashMap<>(counters);
        this.registrations = registrations;
    }

    AdditionalIdentifiers create(ContractConfiguration configuration) {
        var identifiers = new AdditionalIdentifiers(configuration, counters);
        identifiers.onRegistration(registrations);
        return identifiers;
    }

    void completed(AdditionalIdentifiers identifiers) {
        counters = identifiers.counterSnapshot();
    }

    Map<String, Long> counters() { return Map.copyOf(counters); }
}
