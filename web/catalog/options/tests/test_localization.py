import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def read_unique(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Duplicate translation key: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=pairs)


class LocalizationTest(unittest.TestCase):
    def test_languages_cover_contract_dar_and_current_interface(self):
        contract = json.loads((ROOT / 'contract.json').read_text())
        dar = json.loads((ROOT / contract['dar']['catalogue']).read_text())
        required = set()
        def collect(value):
            if isinstance(value, dict):
                for name, child in value.items():
                    if name in {'labelKey', 'helpKey', 'disabledReasonKey', 'choiceDisabledReasonKey'}:
                        required.add(child)
                    elif name.endswith('Keys'):
                        required.update(child.values() if isinstance(child, dict) else child)
                    else:
                        collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(contract)
        for option in contract['options']:
            self.assertNotIn('label', option)
            self.assertNotIn('help', option)
            if 'choices' in option:
                self.assertEqual(set(option['choiceLabelKeys']),
                                 {str(v) for v in option['choices']})
        for field in dar['fields']:
            required.add(contract['dar']['fieldLabelKeyPattern'].format(id=field['id']))
            required.add(contract['dar']['helpKeyPattern'].format(semanticGroup=field['semanticGroup']))
        for code in dar['codes']:
            required.add(contract['dar']['codeLabelKeyPattern'].format(code=code['code']))
        # Include keys used by the interface and its error helpers.
        for source in (ROOT / '../../frontend/src').rglob('*.ts*'):
            required.update(re.findall(r"['\"]((?:app|identifier|dar|document)\.[\w.]+)['\"]", source.read_text()))
        required.update('identifier.coding.' + field for field in ('system', 'code', 'display'))
        required.update('app.outcomes.' + status for status in (
            'succeeded', 'failed', 'cancelled', 'interrupted', 'passed',
            'not-requested', 'incomplete', 'differences', 'unavailable', 'findings'))
        validation = (ROOT / '../../frontend/src/generation-validation.ts').read_text()
        required.update('app.generation.error.' + code for code in
                        re.findall(r"error\([^,]+, '([A-Za-z]+)'", validation))
        required.discard('identifier.typeLabel.')
        required.difference_update({'document.type.mode', 'document.type.values', 'document.nlp.mode', 'document.nlp.values'})
        required.update('document.' + field for field in ('system', 'code', 'display'))
        required.update('document.preset.' + name for name in ('combined', 'kdl', 'xds', 'empty', 'original', 'format', 'surrogated'))
        bindings = json.loads((ROOT / 'identifier-bindings.json').read_text())
        required.update('identifier.typeLabel.' + item['code'] for item in bindings['types'])
        languages = {lang: read_unique(ROOT / filename)
                     for lang, filename in contract['ui']['localization']['files'].items()}
        self.assertEqual(set(languages), set(contract['ui']['localization']['languages']))
        self.assertEqual(contract['ui']['localization']['defaultLanguage'], 'de')
        for lang, texts in languages.items():
            self.assertEqual(set(texts), required, f'Missing or unused keys in {lang}')
            self.assertTrue(all(isinstance(value, str) and value.strip()
                                for value in texts.values()))
        for key in required:
            placeholders = [set(re.findall(r'\{([\w:]+)\}', texts[key]))
                            for texts in languages.values()]
            self.assertEqual(placeholders[0], placeholders[1], key)


if __name__ == '__main__':
    unittest.main()
