"""Short result names using persisted output-patient counts only."""
import re


def patient_count(dataset):
    value = dataset.get('inspection', {}).get('patients')
    return value if type(value) is int and value >= 0 else None


def run_patient_count(manifest):
    # Counts from different datasets cannot be added: patient IDs may overlap.
    items = manifest.get('datasets', [])
    return patient_count(items[0]) if len(items) == 1 else None


def filename(name, patients, fallback):
    label = re.sub(r'[^\w.-]+', '-', name or '', flags=re.UNICODE).strip('.-')[:120]
    count = f'-{patients}Pat' if patients is not None else ''
    return f'{label}{count}.zip' if label else f'synthifhir{count}-{fallback}.zip'
