"""Generate the structural schema from the option and DAR catalogues."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def expanded_fields(contract):
    fields = json.loads((ROOT / contract['dar']['catalogue']).read_text())['fields']
    base = {f['id']: f for f in fields}
    return fields + [{**base[scope['source']], 'id': scope['id']} for scope in contract['dar'].get('scopedFields', [])]


def generate():
    contract = json.loads((ROOT / 'contract.json').read_text())
    # Schema annotations remain stable English metadata; interface language
    # never changes accepted values or generated FHIR content.
    texts = json.loads((ROOT / 'en.json').read_text())
    fields = expanded_fields(contract)
    options = {}
    for option in contract['options']:
        definition = {
            'title': texts[option['labelKey']], 'description': texts[option['helpKey']],
            'default': option['default'],
        }
        kind = option['type']
        if kind == 'enum':
            definition['enum'] = option['choices']
        elif kind == 'set':
            definition.update(type='array', uniqueItems=True,
                              items={'enum': option['choices']})
        else:
            definition['type'] = kind
        if 'items' in option:
            definition['items'] = option['items']
        if 'minimum' in option:
            definition['minimum'] = option['minimum']
        options[option['id']] = definition
    dar = {}
    for field in fields:
        dar[field['id']] = {
            'oneOf': [
                {'type': 'object', 'required': ['mode'],
                 'properties': {'mode': {'const': 'unchanged'}},
                 'additionalProperties': False},
                {'type': 'object', 'required': ['mode', 'code'],
                 'properties': {'mode': {'const': 'overwrite'},
                                'code': {'type': 'string', 'minLength': 1, 'pattern': r'\S'},
                                'onlyWhenMissing': {'type': 'boolean'}},
                 'additionalProperties': False},
            ]
        }
    eligible = sorted({r.get('identifierSelector', r['resourceType']) for r in contract['resources']
                       if r['identifierEligible']} | {s['selector'] for s in contract['identifierScopes']})
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        'title': 'Converter configuration',
        'type': 'object', 'required': ['schemaVersion'],
        'additionalProperties': False,
        'properties': {
            'schemaVersion': {'const': contract['schemaVersion']},
            'values': {'type': 'object', 'properties': options,
                       'additionalProperties': False, 'default': {}},
            'dar': {'type': 'object', 'properties': dar,
                    'additionalProperties': False, 'default': {}},
            'identifierRules': {
                'type': 'array', 'default': [],
                'items': {
                    'type': 'object', 'additionalProperties': False,
                    'required': ['id', 'enabled', 'resources', 'system', 'pattern'],
                    'properties': {
                        'id': {'type': 'string', 'format': 'uuid'},
                        'enabled': {'type': 'boolean'},
                        'resources': {'type': 'array', 'uniqueItems': True,
                                      'minItems': 1, 'items': {'enum': eligible}},
                        'system': {'type': 'string', 'minLength': 1},
                        'pattern': {'type': 'string', 'minLength': 1},
                        'scope': {'type': 'string', 'enum': ['resource', 'case']},
                        'countStart': {'type': 'integer', 'minimum': 1, 'maximum': 9007199254740991, 'default': 1},
                        'use': {'enum': ['', 'usual', 'official', 'temp', 'secondary', 'old']},
                        'typeText': {'type': 'string'},
                        'typeCodings': {'type': 'array', 'items': {
                            'type': 'object', 'additionalProperties': False,
                            'properties': {key: {'type': 'string'} for key in ('system', 'code', 'display')}
                        }},
                    },
                },
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = json.dumps(generate(), indent=2, ensure_ascii=False) + '\n'
    target = ROOT / 'configuration.schema.json'
    if args.check:
        if not target.exists() or target.read_text() != expected:
            raise SystemExit('Configuration schema is stale; regenerate it.')
    else:
        target.write_text(expected)


if __name__ == '__main__':
    main()
