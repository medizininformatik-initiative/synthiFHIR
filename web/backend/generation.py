"""Typed Synthea settings and catalogue from the pinned generator artifact."""
import csv
from datetime import date
from functools import lru_cache
import io
import json
import result_exports
from typing import Literal
import zipfile

from pydantic import BaseModel, ConfigDict, Field, model_validator
import store
import synthea_runtime


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    executionMode: Literal['standard', 'bulk'] = 'standard'
    bulkGzip: bool = Field(default=True, strict=True)
    outputMode: Literal['kds', 'synthea'] = 'kds'
    syntheticConsent: bool = True
    population: int = Field(default=1, ge=1, le=100000, strict=True)
    patientsPerRun: int = Field(default=10000, ge=100, le=10000, strict=True)
    minAge: int = Field(default=18, ge=0, le=140, strict=True)
    maxAge: int = Field(default=80, ge=0, le=140, strict=True)
    gender: Literal['', 'F', 'M'] = ''
    patientSeed: str = Field(default='20260912', pattern=r'^-?\d{1,19}$')
    clinicianSeed: str = Field(default='20260912', pattern=r'^-?\d{1,19}$')
    singlePersonSeed: str = Field(default='', pattern=r'^(-?\d{1,19})?$')
    referenceDate: date = date(2026, 9, 12)
    endDate: date = date(2026, 9, 12)
    state: str = 'Massachusetts'
    city: str = ''
    yearsOfHistory: int = Field(default=5, ge=0, le=140, strict=True)
    patientFilter: Literal['all', 'alive', 'dead'] = 'all'
    overflow: bool = Field(default=False, strict=True)
    modules: list[str] = Field(default_factory=list, max_length=500)
    keepModule: str = ''
    inpatientOnly: bool = Field(default=False, strict=True)
    inpatientMinDays: int = Field(default=2, ge=1, le=36500, strict=True)
    timestepDays: int = Field(default=7, ge=1, le=365, strict=True)
    maxAttempts: int = Field(default=1000, ge=1, le=10000, strict=True)
    veteranPopulation: bool = Field(default=False, strict=True)

    @model_validator(mode='before')
    @classmethod
    def consent_default(cls, value):
        if isinstance(value, dict) and 'syntheticConsent' not in value:
            return {**value, 'syntheticConsent': value.get('outputMode', 'kds') == 'kds'}
        return value

    @model_validator(mode='after')
    def consistent(self):
        if self.patientsPerRun % 100:
            raise ValueError('Patients per subrun must be a multiple of 100')
        if self.executionMode == 'standard' and self.population > 1000:
            raise ValueError('Standard runs support at most 1000 patients; choose bulk generation')
        if self.executionMode == 'bulk' and self.overflow:
            raise ValueError('Bulk generation requires disabled overflow')
        if self.syntheticConsent and self.outputMode != 'kds':
            raise ValueError('Synthetic consent requires KDS output')
        if self.minAge > self.maxAge:
            raise ValueError('Minimum age must not exceed maximum age')
        if self.referenceDate > self.endDate or self.endDate > date.today():
            raise ValueError('Reference date must be on or before the simulation end, which cannot be in the future')
        for seed in [self.patientSeed, self.clinicianSeed, self.singlePersonSeed]:
            if seed and not -(2**63) <= int(seed) < 2**63:
                raise ValueError('Seeds must be signed 64-bit integers')
        if self.singlePersonSeed and self.population != 1:
            raise ValueError('A single-person seed requires a population of one')
        if len(set(self.modules)) != len(self.modules):
            raise ValueError('Choose each module once')
        return self


@lru_cache(maxsize=1)
def catalogue():
    root = synthea_runtime.ROOT
    revision = (root / 'target/synthea-revision.txt').read_text().strip()
    if revision != (root / 'scripts/synthea-version.txt').read_text().strip():
        raise ValueError('The installed Synthea version does not match its mappings')
    with zipfile.ZipFile(root / 'target/synthea.jar') as archive:
        locations = {}
        for row in csv.DictReader(io.StringIO(archive.read('geography/demographics.csv').decode('utf-8-sig'))):
            locations.setdefault(row['STNAME'], set()).add(row['NAME'])
        def modules(prefix):
            result = []
            for name in sorted(set(archive.namelist())):
                if not name.startswith(prefix) or not name.endswith('.json') or '/' in name.removeprefix(prefix):
                    continue
                module = json.loads(archive.read(name))
                examples = {}
                for state in module.get('states', {}).values():
                    kind = state.get('type')
                    if kind in {'ConditionOnset', 'Procedure', 'MedicationOrder', 'Observation', 'Encounter'}:
                        labels = [str(code.get('display') or code.get('code', '')) for code in state.get('codes', [])]
                        examples.setdefault(kind, set()).update(filter(None, labels))
                result.append({'id': name.removeprefix(prefix).removesuffix('.json') if prefix == 'modules/' else name.removeprefix(prefix),
                               'name': module.get('name', name),
                               'examples': {kind: sorted(labels)[:5] for kind, labels in examples.items() if labels}})
            return result
        return {'bulkOutputDirectory': result_exports.LABEL, 'revision': revision, 'sha256': store.digest(root / 'target/synthea.jar'),
                'locations': {state: sorted(cities) for state, cities in sorted(locations.items())},
                'modules': modules('modules/'), 'keepModules': modules('keep_modules/'),
                'defaults': Settings().model_dump(mode='json')}


def normalize(value):
    settings = Settings.model_validate(value)
    catalog = catalogue()
    if settings.state not in catalog['locations'] or (settings.city and settings.city not in catalog['locations'][settings.state]):
        raise ValueError('Choose a state and city from the installed Synthea catalogue')
    if any(module not in {m['id'] for m in catalog['modules']} for module in settings.modules):
        raise ValueError('Unknown Synthea module')
    if settings.keepModule and settings.keepModule not in {m['id'] for m in catalog['keepModules']}:
        raise ValueError('Unknown patient-selection module')
    return settings.model_dump(mode='json')


def arguments(value):
    settings = Settings.model_validate(value)
    result = ['-p', str(settings.population), '-a', f'{settings.minAge}-{settings.maxAge}',
              '-s', settings.patientSeed, '-cs', settings.clinicianSeed,
              '-r', settings.referenceDate.strftime('%Y%m%d'), '-e', settings.endDate.strftime('%Y%m%d'),
              '-o', str(settings.overflow).lower(),
              f'--exporter.years_of_history={settings.yearsOfHistory}',
              '--generate.thread_pool_size=1',
              '--generate.only_alive_patients=' + str(settings.patientFilter == 'alive').lower(),
              '--generate.only_dead_patients=' + str(settings.patientFilter == 'dead').lower(),
              f'--generate.timestep={settings.timestepDays * 86400000}',
              f'--generate.max_attempts_to_keep_patient={settings.maxAttempts}',
              '--generate.veteran_population_override=' + str(settings.veteranPopulation).lower()]
    if settings.inpatientOnly:
        result += ['--generate.workbench_inpatient=true',
                   f'--generate.workbench_inpatient_days={settings.inpatientMinDays}']
    if settings.gender:
        result += ['-g', settings.gender]
    if settings.singlePersonSeed:
        result += ['-ps', settings.singlePersonSeed]
    if settings.modules:
        result += ['-m', ':'.join(settings.modules)]
    if settings.keepModule:
        result += ['-k', settings.keepModule]
    result.append(settings.state)
    if settings.city:
        result.append(settings.city)
    return result


def native_command(settings, destination):
    """Run the pinned generator directly; retain its FHIR resources without projection."""
    return ['java', '-Xmx1536m', '-Duser.timezone=Europe/Berlin', '-jar', str(synthea_runtime.ROOT / 'target/synthea.jar'),
            *arguments(settings), '--exporter.baseDirectory=' + str(destination),
            '--exporter.fhir.export=true', '--exporter.fhir_stu3.export=false',
            '--exporter.fhir_dstu2.export=false', '--exporter.fhir.bulk_data=false',
            '--exporter.use_uuid_filenames=true', '--exporter.hospital.fhir.export=true',
            '--exporter.practitioner.fhir.export=true', '--exporter.csv.export=false', '--exporter.ccda.export=false']


def patient_count(paths):
    # Hospital/practitioner bundles are also present in the unconverted output.
    total = 0
    for path in paths:
        resource = json.loads(path.read_text())
        total += sum(entry.get('resource', {}).get('resourceType') == 'Patient' for entry in resource.get('entry', []))
    return total
