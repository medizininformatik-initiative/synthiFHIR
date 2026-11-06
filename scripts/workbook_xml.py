"""Read existing XLSX cells without opening or rewriting the workbook."""
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def validation_ranges(path):
    """Read original validation coverage for reassignment through UNO, without editing XLSX."""
    with ZipFile(path) as z:
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
            target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            with z.open(target) as xml:
                for _, element in ET.iterparse(xml, events=('end',)):
                    if element.tag == '{' + NS['s'] + '}dataValidation':
                        yield sheet.attrib['name'], element.attrib['sqref']
                    element.clear()

def read_sheets(path):
    with ZipFile(path) as z:
        strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)] if 'xl/sharedStrings.xml' in z.namelist() else []
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets = {}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
            target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            cells = {}
            # Release each parsed row instead of retaining a complete worksheet tree.
            with z.open(target) as xml:
                sheet_data = None
                for event, element in ET.iterparse(xml, events=('start', 'end')):
                    if event == 'start' and element.tag == '{' + NS['s'] + '}sheetData':
                        sheet_data = element
                    elif event == 'end' and element.tag == '{' + NS['s'] + '}row' and sheet_data is not None:
                        for cell in element.findall('s:c', NS):
                            v = cell.find('s:v', NS)
                            value = v.text if v is not None else ''.join(cell.find('s:is', NS).itertext()) if cell.find('s:is', NS) is not None else ''
                            if cell.attrib.get('t') == 's':
                                value = strings[int(value)]
                            if value:
                                cells[cell.attrib['r']] = value
                        sheet_data.remove(element)
                    elif event == 'end' and element is sheet_data:
                        sheet_data = None
            sheets[sheet.attrib['name']] = cells
        return sheets
