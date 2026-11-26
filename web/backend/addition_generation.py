"""Generate new patient-linked test resources from explicit addition settings."""
import base64
from datetime import datetime, timezone
import re
from uuid import uuid5

# FHIR R4 defaults; generated content is explicitly synthetic, not a source copy.
STATUSES = {
    'DocumentReference': ('current', 'superseded', 'entered-in-error'),
    'Condition': ('active', 'recurrence', 'relapse', 'inactive', 'remission', 'resolved'),
    'Observation': ('final', 'registered', 'preliminary', 'amended', 'corrected', 'cancelled', 'entered-in-error', 'unknown'),
    'Procedure': ('completed', 'preparation', 'in-progress', 'not-done', 'on-hold', 'stopped', 'entered-in-error', 'unknown'),
    'MedicationRequest': ('active', 'on-hold', 'cancelled', 'completed', 'entered-in-error', 'stopped', 'draft', 'unknown'),
    'MedicationAdministration': ('completed', 'in-progress', 'not-done', 'on-hold', 'stopped', 'entered-in-error', 'unknown'),
    'MedicationStatement': ('active', 'completed', 'entered-in-error', 'intended', 'stopped', 'on-hold', 'unknown', 'not-taken'),
    'Immunization': ('completed', 'entered-in-error', 'not-done'),
    'DiagnosticReport': ('final', 'registered', 'partial', 'preliminary', 'modified', 'amended', 'corrected', 'appended', 'cancelled', 'entered-in-error', 'unknown'),
    'CarePlan': ('active', 'draft', 'on-hold', 'revoked', 'completed', 'entered-in-error', 'unknown'),
    'Consent': ('active', 'draft', 'proposed', 'rejected', 'inactive', 'entered-in-error'),
}
LABELS = {
    'DocumentReference': 'Synthetischer Testbericht', 'Condition': 'Synthetische Testdiagnose',
    'Observation': 'Synthetischer Testbefund', 'Procedure': 'Synthetische Testprozedur',
    'MedicationRequest': 'Synthetisches Testmedikament', 'MedicationAdministration': 'Synthetisches Testmedikament',
    'MedicationStatement': 'Synthetisches Testmedikament', 'Immunization': 'Synthetischer Testimpfstoff',
    'DiagnosticReport': 'Synthetischer Befundbericht', 'CarePlan': 'Synthetischer Behandlungsplan',
    'Consent': 'Synthetische Testeinwilligung',
}


def validate(settings):
    if settings.status and settings.status not in STATUSES[settings.resourceType]:
        raise ValueError('Invalid status for ' + settings.resourceType)
    if settings.timestamp:
        try:
            parsed = datetime.fromisoformat(settings.timestamp.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError('Use an ISO timestamp with timezone') from error
        if parsed.tzinfo is None or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', settings.timestamp):
            raise ValueError('Use an ISO timestamp with timezone')


def generate(settings, patient, encounter, timestamp, namespace):
    kind = settings.resourceType
    target = encounter or patient
    resource = {'resourceType': kind, 'id': str(uuid5(namespace, kind + ':' + target))}
    status = settings.status or STATUSES[kind][0]
    label = settings.text.strip() or LABELS[kind]
    codings = [{key: value for key, value in row.model_dump().items() if value} for row in settings.codings]
    concept = {'text': label}
    if settings.codingMode != 'input' and any(codings):
        concept['coding'] = [coding for coding in codings if coding]
    date = settings.timestamp or timestamp
    if kind == 'Condition':
        resource['clinicalStatus'] = {'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/condition-clinical', 'code': status}]}
    else:
        resource['status'] = status
    patient_field = 'patient' if kind in {'Immunization', 'Consent'} else 'subject'
    resource[patient_field] = {'reference': patient}
    if encounter:
        if kind == 'DocumentReference':
            resource['context'] = {'encounter': [{'reference': encounter}]}
        else:
            resource['context' if kind in {'MedicationAdministration', 'MedicationStatement'} else 'encounter'] = {'reference': encounter}
    if kind == 'DocumentReference':
        resource.update(type=concept, date=date)
        body = settings.documentText.strip() or f'Synthetisches Testdokument\nPatient: {patient}\nKontakt: {encounter or "ohne Kontaktbezug"}\nZeitpunkt: {date}\n{label}'
        resource['content'] = [{'attachment': {'contentType': 'text/plain', 'language': 'de', 'title': label,
                                               'creation': date, 'data': base64.b64encode(body.encode()).decode()}}]
        if settings.nlpMode != 'omit':
            from resource_additions import NLP_URL, NLP_SYSTEM
            codes = settings.nlpCodes if settings.nlpMode in {'fill', 'replace'} else ['unprocessed']
            if codes:
                resource['extension'] = [{'url': NLP_URL, 'valueCodeableConcept': {'coding': [
                    {'system': NLP_SYSTEM, 'code': code} for code in dict.fromkeys(codes)]}}]
    elif kind == 'Condition':
        resource.update(code=concept, recordedDate=date, onsetDateTime=date)
    elif kind == 'Observation':
        resource.update(code=concept, effectiveDateTime=date, valueString=settings.value.strip() or 'Synthetischer Testwert')
    elif kind == 'Procedure':
        resource.update(code=concept, performedDateTime=date)
    elif kind.startswith('Medication'):
        resource['medicationCodeableConcept'] = concept
        if kind == 'MedicationRequest':
            resource.update(intent='order', authoredOn=date)
        else:
            resource['effectiveDateTime'] = date
    elif kind == 'Immunization':
        resource.update(vaccineCode=concept, occurrenceDateTime=date, primarySource=True)
    elif kind == 'DiagnosticReport':
        resource.update(code=concept, effectiveDateTime=date, conclusion=settings.value.strip() or 'Synthetischer Testbefund')
    elif kind == 'CarePlan':
        resource.update(intent='plan', title=label, description=settings.value.strip() or label, created=date)
    elif kind == 'Consent':
        resource.update(dateTime=date, scope={'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/consentscope', 'code': 'patient-privacy'}]},
                        category=[{'text': label}], policyRule={'text': 'Synthetische Testeinwilligung'}, provision={'type': 'permit'})
    return resource


def fallback_timestamp(created):
    return datetime.fromtimestamp(created, timezone.utc).isoformat(timespec='seconds')
