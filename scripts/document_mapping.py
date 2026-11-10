"""Deterministic document types and processing evidence, shared by Excel and CSV."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from workbook_records import record

NLP_EXTENSION = 'https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/StructureDefinition/mii-ex-dokument-nlp-processing-status'
NLP_SYSTEM = 'https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/CodeSystem/mii-cs-dokument-nlp-processing-status'
PATH = Path(__file__).resolve().parent / 'mappings/synthea-documents.json'


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@lru_cache(maxsize=1)
def catalogue():
    raw = PATH.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def document_blocks(rows):
    """Yield each start row with its continuation rows in source order."""
    block = []
    for row in rows:
        if row['Dokument-ID']:
            if block:
                yield block
            block = []
        elif not block:
            raise ValueError('Document continuation without Dokument-ID')
        block.append(row)
    if block:
        yield block


def finalize_documents(rows, report):
    """Run after text localization; metadata changes do not imply text processing."""
    data, digest = catalogue()
    report['documentMapping'] = {'id': data['id'], 'sha256': digest, 'source': data['source']}
    decisions = {item['sourceId']: item for item in report['documentMappings']}
    output = []
    for block in document_blocks(rows):
        first = block[0]
        decision = decisions[first['Dokument-ID']]
        present = {(c.get('system', ''), c.get('code', '')) for c in decision['sourceTypeCodings']}
        decision['typeRules'] = []
        for rule in data['rules']:
            if not all((c['system'], c['code']) in present for c in rule['requires']):
                continue
            added = []
            for coding in rule['add']:
                key = (coding['system'], coding['code'])
                if key in present:
                    continue
                block.append(record('DocumentReference', {'Dokumentcode': coding['code'],
                    'Dokumentcodesystem': coding['system'], 'Dokumentbezeichner': coding['display']}))
                present.add(key)
                added.append(dict(coding))
            decision['typeRules'].append({'id': rule['id'], 'addedCodings': added,
                                         'approximation': rule['approximation']})
        final_hash = text_hash(first['Dokumenttext'])
        translated = final_hash != decision['identityTextSha256']
        content_changed = translated or decision['identityTextSha256'] != decision['sourceTextSha256']
        statuses = list(decision['sourceNlpStatuses'])
        derived = []
        if content_changed:
            statuses = [code for code in statuses if code != 'unprocessed']
            derived.extend(['preprocessed', 'content-change'])
        if decision['identityReplacements']:
            derived.append('surrogated')
        if not statuses and not derived:
            derived.append('unprocessed')
        for code in derived:
            if code not in statuses:
                statuses.append(code)
        for index, code in enumerate(statuses):
            if index == len(block):
                block.append(record('DocumentReference', {}))
            block[index]['NLPStatus'] = code
        decision.update(targetTextSha256=final_hash, translationChanged=translated,
                        contentChanged=content_changed, derivedNlpStatuses=derived,
                        outputNlpStatuses=statuses,
                        removedNlpStatuses=['unprocessed'] if content_changed and 'unprocessed' in decision['sourceNlpStatuses'] else [])
        output.extend(block)
    rows[:] = output
