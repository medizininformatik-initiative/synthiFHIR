import re
import archive_names
from uuid import UUID
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field
import store
import configurations
import inputs
import datasets
import service_status
import fhir_uploads
import generation
import run_cleanup
import run_inputs
from starlette.background import BackgroundTask
from generation import Settings as GenerationSettings
from starlette.concurrency import run_in_threadpool

app = FastAPI(title="synthiFHIR Workbench")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "api", "testserver"])


@app.middleware("http")
async def local_mutations(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        if urlsplit(origin).netloc != request.headers.get("host"):
            return PlainTextResponse("Cross-origin changes are not allowed", status_code=403)
    return await call_next(request)



class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID | None = None
    datasetName: str | None = Field(default=None, max_length=200)
    generation: GenerationSettings | None = None
    source: str = "starter"
    diagnostics: bool = False
    tableFormat: Literal["csv", "excel"] | None = None
    configurationProperties: str | None = Field(default=None, min_length=1, max_length=1_000_000)


def directory(job_id):
    try:
        UUID(job_id)
    except ValueError:
        raise HTTPException(404, "Job not found")
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return store.ROOT / "jobs" / job_id, job


@app.get("/api/bulk-resources")
def bulk_resources_status():
    import bulk_resources
    return bulk_resources.status()


@app.get("/api/catalog")
def catalog():
    return {"sources": list(store.SOURCES), "profiles": ["default"]}


@app.get("/api/jobs")
def jobs():
    return store.jobs()


@app.post("/api/jobs", status_code=201)
def create(request: JobRequest):
    try:
        job_id = store.create(request.source, "default", request.configurationProperties, str(request.requestId) if request.requestId else None,
                              request.generation.model_dump(mode='json') if request.generation else None, request.datasetName, table_format=request.tableFormat, diagnostics=request.diagnostics)
    except store.SubmissionConflict as error:
        raise HTTPException(409, str(error))
    except ValueError as error:
        raise HTTPException(422, str(error))
    return store.get(job_id)


class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID | None = None
    population: int = Field(ge=1, le=100000, strict=True)


@app.get('/api/jobs/{job_id}/continuation')
def continuation_preview(job_id: UUID):
    import bulk_continuation
    return configuration_call(bulk_continuation.preview, str(job_id))


@app.post('/api/jobs/{job_id}/continuation', status_code=201)
def continue_job(job_id: UUID, request: ContinuationRequest):
    import bulk_continuation
    result = configuration_call(bulk_continuation.create, str(job_id), request.population,
                                str(request.requestId) if request.requestId else None)
    return store.get(result)


@app.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel(job_id: str):
    directory(job_id)
    store.cancel(job_id)
    return store.get(job_id)


@app.post('/api/jobs/{job_id}/resume', status_code=202)
def resume_job(job_id: UUID):
    try:
        store.resume(str(job_id))
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))
    except store.SubmissionConflict as error:
        raise HTTPException(409, str(error))
    except ValueError as error:
        raise HTTPException(422, str(error))
    return store.get(str(job_id))


@app.delete('/api/jobs/{job_id}', status_code=204)
def delete_job(job_id: UUID):
    try:
        store.delete(str(job_id))
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))
    except store.SubmissionConflict as error:
        raise HTTPException(409, str(error))


@app.post('/api/jobs/{job_id}/cleanup')
def cleanup_job(job_id: UUID):
    return configuration_call(run_cleanup.cleanup, str(job_id))


@app.get("/api/jobs/{job_id}/logs", response_class=PlainTextResponse)
def logs(job_id: str):
    folder, _ = directory(job_id)
    path = folder / "converter.log"
    if not path.exists():
        return "" if (folder / "cleanup.json").is_file() else "Waiting for worker…"
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 128 * 1024))
        return stream.read().decode("utf-8", errors="replace")


@app.get("/api/jobs/{job_id}/snapshot")
def snapshot(job_id: str):
    folder, _ = directory(job_id)
    return FileResponse(folder / "snapshot.json", filename="snapshot.json")


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    folder, job = directory(job_id)
    if not job["download_available"]:
        raise HTTPException(409, "Result archive is not available")
    if (folder / 'cleanup.json').is_file():
        path = run_cleanup.archive(job_id)
        return FileResponse(path, filename=archive_names.filename(job.get('dataset_name'), archive_names.patient_count(path, result=True), job_id),
                            background=BackgroundTask(path.unlink, missing_ok=True))
    return FileResponse(folder / "result.zip", filename=archive_names.filename(job.get('dataset_name'), archive_names.patient_count(folder / 'result.zip', result=True), job_id))


@app.get('/api/jobs/{job_id}/inputs-download')
def inputs_download(job_id: UUID):
    try:
        path = run_inputs.archive(str(job_id))
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))
    except store.SubmissionConflict as error:
        raise HTTPException(409, str(error))
    return FileResponse(path, filename=f'synthifhir-inputs-{job_id}.zip',
                        background=BackgroundTask(path.unlink, missing_ok=True))


# Configuration content uses the same versioned Properties contract as the editor.


class ConfigurationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    configurationProperties: str = Field(min_length=1, max_length=1_000_000)


class ConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    configurationProperties: str | None = Field(default=None, min_length=1, max_length=1_000_000)


class ConfigurationDuplicate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)
    name: str = Field(min_length=1, max_length=120)


class ConfigurationDelete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


def configuration_call(action, *args):
    try:
        return action(*args)
    except FileNotFoundError:
        raise HTTPException(404, 'Configuration not found')
    except configurations.Conflict as error:
        raise HTTPException(409, str(error))
    except store.SubmissionConflict as error:
        raise HTTPException(409, str(error))
    except ValueError as error:
        raise HTTPException(422, str(error))


@app.get('/api/configurations')
def list_configurations():
    return configurations.summaries()


@app.get('/api/configurations/{configuration_id}')
def get_configuration(configuration_id: str):
    return configuration_call(configurations.get, configuration_id)


@app.post('/api/configurations', status_code=201)
def create_configuration(request: ConfigurationCreate):
    return configuration_call(configurations.create, request.name, request.configurationProperties)


@app.patch('/api/configurations/{configuration_id}')
def update_configuration(configuration_id: str, request: ConfigurationUpdate):
    return configuration_call(configurations.update, configuration_id, request.revision,
                              request.name, request.configurationProperties)


@app.post('/api/configurations/{configuration_id}/duplicate', status_code=201)
def duplicate_configuration(configuration_id: str, request: ConfigurationDuplicate):
    return configuration_call(configurations.duplicate, configuration_id, request.revision, request.name)


@app.delete('/api/configurations/{configuration_id}', status_code=204)
def delete_configuration(configuration_id: str, request: ConfigurationDelete):
    configuration_call(configurations.delete, configuration_id, request.revision)


@app.post('/api/jobs/{job_id}/excel-editor')
def load_job_excel_editor(job_id: UUID):
    import run_replay
    return configuration_call(run_replay.editor, str(job_id))


@app.post('/api/jobs/{job_id}/editor')
def load_job_editor(job_id: str):
    directory(job_id)
    return configuration_call(store.editor_input, job_id)


@app.get('/api/inputs')
def list_inputs():
    return inputs.summaries()


@app.get('/api/inputs/{input_id}/workbooks/{index}')
def download_input_workbook(input_id: UUID, index: int):
    with inputs.library_lock():
        try:
            manifest_path, _, kind = inputs.resolve('upload:' + str(input_id))
            if kind != 'workbook-set':
                raise ValueError('No saved workbook set')
            import json
            books = json.loads(manifest_path.read_text())['workbooks']
            if index < 0 or index >= len(books):
                raise ValueError('Workbook not found')
            name = books[index]['name']
            content = (manifest_path.parent / 'workbooks' / name).read_bytes()
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(404, str(error))
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe='')})


@app.delete('/api/inputs/{input_id}', status_code=204)
def delete_input(input_id: UUID):
    try:
        inputs.delete(str(input_id))
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))


@app.post('/api/inputs', status_code=201)
async def upload_input(request: Request, filename: str):
    try:
        name = inputs.checked_name(filename)
        with inputs.incoming() as directory:
            size = 0
            with (directory / inputs.input_filename(inputs.kind_for_name(name))).open('wb') as target:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > inputs.MAX_UPLOAD:
                        raise HTTPException(413, 'The input exceeds 64 MiB')
                    target.write(chunk)
            return await run_in_threadpool(inputs.publish, directory, name)
    except ValueError as error:
        raise HTTPException(422, str(error))


@app.get('/api/generation-catalogue')
def generation_catalogue():
    return generation.catalogue()


@app.get('/api/jobs/{job_id}/artifacts')
def job_artifacts(job_id: str):
    _, job = directory(job_id)
    return datasets.get(job_id) if job['state'] in {'succeeded', 'failed'} else {'datasets': [], 'artifacts': []}


@app.get('/api/jobs/{job_id}/artifacts/{artifact_id}')
def artifact_download(job_id: str, artifact_id: str):
    _, job = directory(job_id)
    if job['state'] not in {'succeeded', 'failed'}:
        raise HTTPException(409, 'Run has not ended')
    try:
        path = datasets.resolve_artifact(job_id, artifact_id)
        return FileResponse(path, filename=path.name)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))


@app.get('/api/datasets')
def list_datasets():
    return [{**item, 'jobId': job['id'], 'state': job['state'], 'created': job['created'],
             'summary': job.get('summary'), 'generation': job.get('generation'), 'inputKind': job.get('input_kind'),
             'datasetName': job.get('dataset_name'), 'source': job.get('source'), 'sourceName': job.get('source_name'), 'configuration': job.get('configuration')}
            for job in store.jobs() if job['state'] in {'succeeded', 'failed'} for item in datasets.get(job['id'])['datasets']]


@app.get('/api/datasets/{dataset_id}/statistics')
def dataset_statistics(dataset_id: str):
    try:
        _, item, _ = datasets.resolve_dataset(dataset_id)
        statistics = item.get('statistics')
        if statistics is None:
            raise HTTPException(404, 'Statistics are not available for this dataset')
        import package_statistics
        label = item.get('filename', item['name'])
        return PlainTextResponse(package_statistics.render(label, statistics, kind="Package" if item.get("kind") == "bulk" else "Dataset"), headers={
            'Content-Disposition': f'attachment; filename="{dataset_id}-statistics.txt"'})
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))


@app.get('/api/datasets/{dataset_id}/download')
def dataset_download(dataset_id: str):
    try:
        job, item, path = datasets.resolve_dataset(dataset_id)
        if not path.is_file():
            raise HTTPException(409, 'Dataset archive is not available')
        count = item.get('inspection', {}).get('patients')
        if count is None:
            count = archive_names.patient_count(path)
        if item.get('kind') == 'bulk':
            if path.name.endswith(('.ndjson.gz', '.ndjson')):
                name = path.name
            else:
                stem = re.sub(r'-\d+Pat$', '', path.stem)
                name = f'{stem}-{count}Pat.zip'
        else:
            name = archive_names.filename(job.get('dataset_name'), count, dataset_id)
        return FileResponse(path, filename=name)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))

    except store.SubmissionConflict as error:
        raise HTTPException(409, str(error))


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requestId: UUID
    target: str
    datasets: list[str] = Field(min_length=1, max_length=10000)


@app.get('/api/services')
def services():
    return service_status.services()


@app.get('/api/fhir-targets')
def fhir_targets():
    return fhir_uploads.targets()


@app.get('/api/fhir-uploads')
def fhir_upload_history(limit: int = Query(default=5, ge=1, le=10000)):
    return fhir_uploads.history_page(limit)


@app.delete('/api/fhir-uploads', status_code=204)
def delete_fhir_upload_history():
    configuration_call(fhir_uploads.delete_history)


@app.delete('/api/fhir-uploads/{upload_id}', status_code=204)
def delete_fhir_upload(upload_id: UUID):
    configuration_call(fhir_uploads.delete_history, str(upload_id))


@app.post('/api/fhir-uploads', status_code=202)
def fhir_upload(request: UploadRequest):
    return configuration_call(fhir_uploads.create, str(request.requestId), request.target, request.datasets)


@app.post('/api/fhir-uploads/{upload_id}/resume', status_code=202)
def resume_fhir_upload(upload_id: UUID):
    return configuration_call(fhir_uploads.resume, str(upload_id))


@app.post('/api/fhir-uploads/{upload_id}/cancel', status_code=202)
def cancel_fhir_upload(upload_id: UUID):
    configuration_call(fhir_uploads.cancel, str(upload_id))
    return fhir_uploads.get(str(upload_id))


@app.get('/api/fhir-uploads/{upload_id}/logs', response_class=PlainTextResponse)
def fhir_upload_logs(upload_id: UUID):
    return configuration_call(fhir_uploads.logs, str(upload_id))
