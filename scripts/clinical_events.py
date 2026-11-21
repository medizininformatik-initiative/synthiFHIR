"""Additional clinical sheets. Structured details outside these columns are reported."""
from workbook_records import record, SCHEMAS
from clinical_import import coding, UnsupportedValue
import base64
import hashlib
from german_demographics import localize_document_identity
from german_texts import GermanTexts
from document_mapping import NLP_EXTENSION, NLP_SYSTEM, text_hash
from vaccine_mapping import map_vaccine, metadata as vaccine_metadata

SHEETS = {'Immunization': 'Impfung', 'DiagnosticReport': 'Befundbericht', 'CarePlan': 'Behandlungsplan'}


def prepare_events(entries, pid, encounters, imported_observations, year=2026, *, diagnostics=True):
    rows = {s: [] for s in SHEETS.values()}; imported, losses, vaccines = [], [], []
    texts = GermanTexts()
    index = {}
    for entry in entries:
        r = entry.get('resource', {})
        if r.get('id'):
            index[r['resourceType']+'/'+r['id']] = r
            if entry.get('fullUrl'): index[entry['fullUrl']] = r
    for entry in entries:
        r = entry.get('resource', {}); typ = r.get('resourceType')
        if typ not in SHEETS: continue
        def loss(path, reason):
            if diagnostics: losses.append({'resourceType':typ,'id':r.get('id'),'path':path,'reason':reason})
        patient = r.get('patient', r.get('subject', {})).get('reference')
        if patient and (patient not in index or index[patient]['id'] != pid): raise ValueError('Broken patient reference '+patient)
        encounter = r.get('encounter', {}).get('reference')
        if encounter and (encounter not in index or index[encounter]['id'] not in encounters): raise ValueError('Broken encounter '+encounter)
        nr = encounters[index[encounter]['id']] if encounter else ''
        if typ == 'CarePlan':
            from careplan_import import project_plan
            first_row = len(rows['Behandlungsplan']) + 1
            rows['Behandlungsplan'].extend(project_plan(r, pid, nr, loss))
            imported.append({'sourceId': r['id'], 'resourceType': typ, 'inputRow': first_row})
            continue
        values = {'Patient-ID': pid, 'Fall-Nr': nr, 'Eintrag ID': r['id']}
        handled = {'resourceType','id','subject','patient','encounter'}
        try:
            cc = (r.get('vaccineCode') if typ=='Immunization'
                  else r.get('code'))
            code, system, label = coding(cc or {})
            if len((cc or {}).get('coding',[]))>1:loss('code.coding[1:]','Erstes Coding übernommen; weitere Codings nicht dargestellt')
            values.update({'Code':code,'Codesystem':system,'Bezeichner':label})
            handled.add('vaccineCode' if typ=='Immunization' else 'code')
            for source, target in [('status','Status'),('intent','Absicht')]:
                if target in SCHEMAS[SHEETS[typ]]:
                    values[target] = r.get(source,''); handled.add(source)
            if typ=='Immunization':
                decision = map_vaccine(cc['coding'][0], year)
                vaccines.append({'sourceId': r['id'], **decision})
                if cc['coding'][0]['system'] == 'http://hl7.org/fhir/sid/cvx':
                    values['Bezeichner'] = texts.text(label, 'Impfung', system, code)
                    target = decision['target']
                    values['Code'] = target['code'] if target else ''
                    values['Codesystem'] = 'ATC ' + target['version'] if target else ''
                    if not target: values['Bezeichner'] += ' (Impfstoffzuordnung offen)'
                values['Zeitpunkt']=r.get('occurrenceDateTime','')
                values['Primärquelle']=str(r['primarySource']).lower() if 'primarySource'in r else ''
                handled.update(['occurrenceDateTime','primarySource'])
            elif typ=='DiagnosticReport':
                values['Zeitpunkt']=r.get('effectiveDateTime','');values['Ausgabezeitpunkt']=r.get('issued','')
                results=[]
                for ref in r.get('result',[]):
                    found=index.get(ref.get('reference'))
                    if found and found.get('id') in imported_observations:results.append(found['id'])
                    else:loss('result','Nicht übernommener oder nicht auflösbarer Ergebnisverweis ausgelassen')
                values['Ergebnisse']=';'.join(results);values['Beschreibung']=r.get('conclusion','')
                # Presented notes remain in the DocumentReference input; no
                # attachment text is relabelled as a diagnostic conclusion.
                handled.update(['effectiveDateTime','issued','result','conclusion'])
            rows[SHEETS[typ]].append(record(SHEETS[typ], values))
            imported.append({'sourceId':r['id'],'resourceType':typ})
            for key in r.keys()-handled:loss(key,'Eigenschaft nicht oder nur teilweise übernommen')
        except (UnsupportedValue, KeyError) as ex:loss('$','Ressource ausgelassen: '+str(ex))
    return rows, {'clinicalImports':imported, 'losses':losses,
                  'vaccineMapping': vaccine_metadata(), 'vaccineMappings': vaccines,
                  'vaccineTexts': texts.report()}



def prepare_documents(entries, pid, encounters, *, diagnostics=True):
    rows=[];imports=[];losses=[];identity_changes=[];document_mappings=[]
    index={}
    for e in entries:
        r=e.get('resource',{})
        if r.get('id'):
            index[r['resourceType']+'/'+r['id']]=r
            if e.get('fullUrl'):index[e['fullUrl']]=r
    for e in entries:
        r=e.get('resource',{})
        if r.get('resourceType')!='DocumentReference':continue
        def loss(path,reason):
            if diagnostics: losses.append({'resourceType':'DocumentReference','id':r['id'],'path':path,'reason':reason})
        patient=index.get(r.get('subject',{}).get('reference'))
        if patient is None or patient.get('id')!=pid:raise ValueError('Broken document patient reference')
        refs=r.get('context',{}).get('encounter',[])
        nr=''
        if refs:
            enc=index.get(refs[0].get('reference'))
            if enc is None or enc.get('id')not in encounters:raise ValueError('Broken document encounter reference')
            nr=encounters[enc['id']]
        try:
            attachments=r.get('content',[])
            if not attachments:raise UnsupportedValue('Document content is missing')
            a=attachments[0]['attachment']
            if not a.get('contentType','').startswith('text/plain'):raise UnsupportedValue('Document import requires embedded plain text')
            text=base64.b64decode(a.get('data',''),validate=True).decode('utf-8')
            original = text
            text, replacements = localize_document_identity(text, patient)
            if not text or len(text)>32767:raise UnsupportedValue('Document is empty or exceeds the Excel cell size limit')
            source_codings = r.get('type', {}).get('coding', [])
            document_rows = [record('DocumentReference', {
                'Dokument-ID': r['id'],
                'Patient-ID': pid,
                'Fall-Nr': nr,
                'Embed': 'ja',
                'Dokumenttext': text,
                'Status': r.get('status',''),
                'Ausgabezeitpunkt': r.get('date',''),
            })]
            for position, source_coding in enumerate(source_codings):
                if position:
                    document_rows.append(record('DocumentReference', {}))
                document_rows[-1].update(Dokumentcode=source_coding.get('code', ''),
                    Dokumentcodesystem=source_coding.get('system', ''),
                    Dokumentbezeichner=source_coding.get('display', ''))
                for key in source_coding.keys() - {'system', 'code', 'display'}:
                    loss(f'type.coding[{position}].{key}', 'Coding metadata retained in documentMappings, not represented in the table')
            if r.get('type', {}).get('text'):
                if source_codings:
                    document_rows.append(record('DocumentReference', {}))
                document_rows[-1]['Dokumentbezeichner'] = r['type']['text']
            for key in r.get('type', {}).keys() - {'coding', 'text'}:
                loss('type.' + key, 'Document type metadata is not represented in the table')
            statuses = []
            for extension in r.get('extension', []):
                if extension.get('url') != NLP_EXTENSION:
                    loss('extension', 'Extension outside the supported NLP status projection')
                    continue
                if not isinstance(extension.get('valueCodeableConcept'), dict):
                    loss('extension', 'NLP extension requires valueCodeableConcept for table projection')
                    continue
                for status in extension.get('valueCodeableConcept', {}).get('coding', []):
                    if status.get('system') == NLP_SYSTEM and status.get('code'):
                        statuses.append(status['code'])
                    else:
                        loss('extension.valueCodeableConcept.coding', 'NLP coding without the supported system and a code')
                if extension.get('valueCodeableConcept', {}).keys() - {'coding'} or any(
                        status.keys() - {'system', 'code'} for status in extension.get('valueCodeableConcept', {}).get('coding', [])):
                    loss('extension.valueCodeableConcept', 'Only NLP system and codes are represented in the table')
            rows.extend(document_rows)
            document_mappings.append({'sourceId': r['id'],
                'sourceTypeCodings': [dict(c) for c in source_codings],
                'sourceNlpStatuses': statuses, 'identityReplacements': replacements,
                'sourceTextSha256': text_hash(original), 'identityTextSha256': text_hash(text)})
            imports.append({'resourceType':'DocumentReference','sourceId':r['id']})
            identity_changes.append({'sourceId':r['id'], 'replacements':replacements,
                'sourceTextSha256':hashlib.sha256(original.encode()).hexdigest(),
                'targetTextSha256':hashlib.sha256(text.encode()).hexdigest(),
                'translation':'Final localized text and processing statuses are recorded in documentMappings'})
            if replacements:loss('content.attachment.data','Synthetic identity replacements recorded in documentMappings')
            for k in r.keys()-{'resourceType','id','subject','status','date','type','extension'}:
                loss(k,'Partially imported: plain text attachment and first encounter; additional metadata are not represented')
        except (UnsupportedValue,ValueError,KeyError)as ex:loss('$','Ressource ausgelassen: '+str(ex))
    return rows,{'clinicalImports':imports,'losses':losses,'documentIdentityChanges':identity_changes,'documentMappings':document_mappings}
