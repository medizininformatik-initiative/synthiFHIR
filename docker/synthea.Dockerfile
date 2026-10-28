FROM maven:3.8.5-openjdk-17 AS synthea-build
COPY scripts/synthea-version.txt /synthea-version.txt
COPY docker/synthea-dependencies.gradle /synthea-dependencies.gradle
WORKDIR /synthea
RUN git init && git remote add origin https://github.com/astruebi/synthea.git \
    && git fetch --depth 1 origin "$(cat /synthea-version.txt)" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$(cat /synthea-version.txt)" \
    && ./gradlew --no-daemon --init-script /synthea-dependencies.gradle shadowJar

FROM maven:3.8.5-openjdk-17 AS build
WORKDIR /build
COPY pom.xml ./
COPY src ./src
COPY web/catalog/options/contract.json web/catalog/options/contract.json
COPY web/catalog/dar/generated/catalog.json web/catalog/dar/generated/catalog.json
COPY FHIR_Testdatengenerator_Vorlage.xlsx FHIR_Testdatengenerator_Interpolar_Demo.xlsx ./
COPY input/FHIR_Testdatengenerator_Vorlage.xlsx input/
RUN --mount=type=cache,id=synthifhir-maven,target=/root/.m2,sharing=locked \
    mvn -B test package

FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates tzdata python3 openjdk-17-jdk-headless libreoffice-calc \
    libreoffice-java-common fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/timezone /etc/localtime \
    && cp /usr/share/zoneinfo/Etc/UTC /etc/localtime
ENV LANG=C.UTF-8
WORKDIR /app
COPY --from=build /build/target/synthifhir.jar ./target/synthifhir.jar
COPY scripts ./scripts
COPY web/catalog/dar/generated/catalog.json ./web/catalog/dar/generated/catalog.json
COPY web/catalog/options/contract.json ./web/catalog/options/contract.json
COPY src/main/resources/workbook-absent-reasons.json ./src/main/resources/workbook-absent-reasons.json
COPY src/main/resources/ucum ./src/main/resources/ucum
COPY third-party ./third-party
COPY LICENSE FHIR_Testdatengenerator_Vorlage.xlsx ./
RUN PYTHONPATH=/app/scripts python3 -c "from check_dar import catalogue; catalogue()"
ENTRYPOINT ["python3", "/app/scripts/run_synthea_cases.py"]

# Opt-in build target for the full workflow. The default target below retains
# the existing interface used by the importer and its CI job.
FROM runtime AS workflow
COPY --from=synthea-build /synthea/build/libs/synthea-with-dependencies.jar ./target/synthea.jar
COPY --from=synthea-build /synthea-version.txt ./target/synthea-revision.txt
COPY --from=synthea-build /synthea/LICENSE /synthea/NOTICE ./third-party/synthea/
ENTRYPOINT ["python3", "/app/scripts/run_synthea_container.py"]

FROM runtime AS importer
