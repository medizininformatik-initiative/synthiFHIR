"""Real Synthea standard runs and continuations in isolated temporary storage."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import bulk_continuation as series
import result_exports
import store
import worker

RULE = '4797e210-671c-4200-9bfb-90faaee082f0'
CONFIG = ('CONFIGURATION_VERSION=1\nOUTPUT_FORMATS=JSON,NDJSON\nCHECKS_FHIR_VALIDATION=false\n'
          f'IDENTIFIER_RULE_1_ID={RULE}\nIDENTIFIER_RULE_1_ENABLED=true\n'
          'IDENTIFIER_RULE_1_RESOURCES=Patient\nIDENTIFIER_RULE_1_SYSTEM=urn:series-smoke\n'
          'IDENTIFIER_RULE_1_PATTERN={count}\n')


def run():
    with tempfile.TemporaryDirectory(prefix='standard-series-') as temporary:
        root = Path(temporary)
        with patch.object(store, 'ROOT', root / 'data'), patch.object(result_exports, 'ROOT', root / 'results'):
            def execute(job):
                assert store.claim() == job
                worker.execute(job)
                folder = store.ROOT / 'jobs' / job
                assert store.get(job)['state'] == 'succeeded', (folder / 'converter.log').read_text()[-12000:]
                assert store.get(job)['series_continuable']
                return folder

            for mode in ('kds', 'synthea'):
                job = store.create('synthea-generation', 'default', CONFIG if mode == 'kds' else None,
                                   generation_settings={'population': 2, 'outputMode': mode, 'patientSeed': '20260912'},
                                   dataset_name='Standard continuation smoke')
                first = execute(job)
                preview = series.preview(job)
                assert preview['generation']['executionMode'] == 'standard'
                assert preview['completedBefore'] == 2
                assert preview['generation']['patientSeed'] == '20260913'
                if mode == 'kds':
                    assert preview['nextCounters'] == {RULE: '3'}, preview
                child = series.create(job, 1)
                second = execute(child)
                assert series.preview(job)['completedBefore'] == 3
                assert set(json.loads((first / 'series-patients.json').read_text())).isdisjoint(
                    json.loads((second / 'series-patients.json').read_text()))
                if mode == 'kds':
                    assert series.preview(child)['nextCounters'] == {RULE: '4'}
                    registrations = [json.loads(line) for line in (second / 'series-registrations.jsonl').read_text().splitlines()]
                    assert {r['value'] for r in registrations if r['system'] == 'urn:series-smoke'} == {'3'}
                assert json.loads((second / 'snapshot.json').read_text())['generation']['executionMode'] == 'standard'
                assert (second / 'result.zip').is_file()
                print(f'{mode}: 2 + 1 patients, distinct sources, preserved mode, continued counters: OK', flush=True)


run()
