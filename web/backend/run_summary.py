"""Read the saved table population and case rows for compact run labels."""
from functools import lru_cache
import json
import csv
import io
import xml.etree.ElementTree as ET
import zipfile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


@lru_cache(maxsize=2048)
def workbook_counts(filename, modified, size):
    with zipfile.ZipFile(filename) as archive:
        strings = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', NS)] if 'xl/sharedStrings.xml' in archive.namelist() else []
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        patients, cases = set(), set()
        for sheet in ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
            if sheet.attrib['name'] not in {'Person', 'Fall'}:
                continue
            target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            for row in ET.fromstring(archive.read(target)).findall('s:sheetData/s:row', NS):
                if row.attrib.get('r') == '1':
                    continue
                cells = {}
                for cell in row.findall('s:c', NS):
                    column = ''.join(c for c in cell.attrib['r'] if c.isalpha())
                    if column not in {'A', 'B'}:
                        continue
                    value = cell.find('s:v', NS)
                    inline = cell.find('s:is', NS)
                    text = value.text if value is not None else ''.join(inline.itertext()) if inline is not None else ''
                    if cell.attrib.get('t') == 's' and text:
                        text = strings[int(text)]
                    cells[column] = (text or '').strip()
                if cells.get('A'):
                    if sheet.attrib['name'] == 'Person':
                        patients.add(cells['A'])
                    elif cells.get('B'):
                        cases.add((cells['A'], cells['B']))
        return frozenset(patients), frozenset(cases)


@lru_cache(maxsize=2048)
def csv_counts(filename, modified, size):
    patients, cases = set(), set()
    with zipfile.ZipFile(filename) as archive:
        for name in archive.namelist():
            if not name.endswith(('_Person.csv', '_Fall.csv')):
                continue
            with archive.open(name) as member:
                rows = csv.reader(io.TextIOWrapper(member, encoding='utf-8-sig'))
                next(rows, None)
                for row in rows:
                    if row and row[0].strip():
                        if name.endswith('_Person.csv'):
                            patients.add(row[0].strip())
                        elif len(row) > 1 and row[1].strip():
                            cases.add((row[0].strip(), row[1].strip()))
    return frozenset(patients), frozenset(cases)


def summary(folder, snapshot, state):
    if state not in {'succeeded', 'failed'}:
        return {}
    if snapshot.get('addition'):
        return {key: snapshot['addition']['counts'][key] for key in ('patients', 'cases')}
    kind = snapshot.get('inputKind', 'workbook')
    books = [folder / 'input.xlsx'] if kind == 'workbook' else sorted((folder / 'output').glob('run-*/excel/*.xlsx'))
    if kind == 'workbook-set':
        books = sorted((folder / 'workbooks').glob('*.xlsx'))
    count = workbook_counts
    if snapshot.get('tableFormat') == 'csv':
        books = sorted((folder / 'output').glob('run-*/csv.zip'))
        count = csv_counts
    if books:
        patients, cases = set(), set()
        try:
            for book in books:
                stat = book.stat()
                people, visits = count(str(book), stat.st_mtime_ns, stat.st_size)
                patients.update(people)
                cases.update(visits)
            return {'patients': len(patients), 'cases': len(cases)}
        except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, csv.Error, ET.ParseError):
            return {}  # Unreadable/unfinished inputs have no reliable counts.
    if snapshot.get('generation', {}).get('outputMode') == 'synthea':
        path = folder / 'datasets.json'
        if path.is_file():
            items = json.loads(path.read_text()).get('datasets', [])
            if len(items) == 1 and 'inspection' in items[0]:
                inspection = items[0]['inspection']
                return {'patients': inspection.get('patients'), 'cases': inspection.get('resourceCounts', {}).get('Encounter', 0)}
    return {}
