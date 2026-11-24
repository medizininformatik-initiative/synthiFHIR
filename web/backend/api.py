import re
import logging
import archive_names
from uuid import UUID
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field
import store
import configurations
import inputs
import datasets
import service_status
import fhir_uploads
import fhir_servers
import generation
import run_cleanup
import run_inputs
from starlette.background import BackgroundTask
from generation import Settings as GenerationSettings
from starlette.concurrency import run_in_threadpool

class OAuthAccessLogFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn's access record: client, method, path+query, HTTP version, status.
        # Keep request diagnostics while omitting one-time authorization codes.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, version, status = record.args
            if isinstance(path, str) and path.startswith('/api/fhir-oauth/'):
                record.args = (client, method, path.split('?', 1)[0], version, status)
        return True


logging.getLogger('uvicorn.access').addFilter(OAuthAccessLogFilter())


app = FastAPI(title="synthiFHIR Workbench")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "api", "testserver"])


@app.middleware("http")
async def local_mutations(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        if urlsplit(origin).netloc != request.headers.get("host"):
            return PlainTextResponse("Cross-origin changes are not allowed", status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/fhir'):
        response.headers['Cache-Control'] = 'no-store'
    return response



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
    count = archive_names.run_patient_count(datasets.get(job_id))
    if (folder / 'cleanup.json').is_file():
        path = run_cleanup.archive(job_id)
        return FileResponse(path, filename=archive_names.filename(job.get('dataset_name'), count, job_id),
                            background=BackgroundTask(path.unlink, missing_ok=True))
    return FileResponse(folder / "result.zip", filename=archive_names.filename(job.get('dataset_name'), count, job_id))


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


@app.get('/api/jobs/{job_id}/fhir-download')
def fhir_download(job_id: UUID):
    import fhir_downloads
    try:
        entries = fhir_downloads.plan(str(job_id))
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))
    except (store.SubmissionConflict, ValueError) as error:
        raise HTTPException(409, str(error))
    return StreamingResponse(fhir_downloads.stream(str(job_id), entries), media_type='application/zip',
                             headers={'Content-Disposition': f'attachment; filename="FHIR-{job_id}.zip"'})


@app.get('/api/jobs/{job_id}/source-download')
def source_download(job_id: UUID):
    import json
    folder, _ = directory(str(job_id))
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    try:
        path = folder / inputs.input_filename(snapshot.get('inputKind', 'workbook'))
    except ValueError:
        raise HTTPException(404, 'Input is unavailable')
    if not path.is_file() or path.is_symlink():
        raise HTTPException(404, 'Input is unavailable')
    return FileResponse(path, filename=path.name)


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
        count = archive_names.patient_count(item)
        if item.get('kind') == 'bulk':
            if path.name.endswith(('.ndjson.gz', '.ndjson')):
                name = path.name
            else:
                stem = re.sub(r'-\d+Pat$', '', path.stem)
                name = archive_names.filename(stem, count, dataset_id)
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


class ServerRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=120)
    address: str = Field(max_length=2048)
    auth: Literal['none', 'basic', 'bearer', 'oauth2']
    username: str = ''
    password: str = ''
    token: str = ''
    flow: str = 'client_credentials'
    issuer: str = ''
    tokenUrl: str = ''
    authorizationUrl: str = ''
    clientId: str = ''
    clientSecret: str = ''
    scope: str = ''


@app.post('/api/fhir-targets', status_code=201)
def create_fhir_target(body: ServerRequest):
    return configuration_call(fhir_servers.save, body.model_dump())


@app.put('/api/fhir-targets/{identifier}')
def update_fhir_target(identifier: UUID, body: ServerRequest):
    return configuration_call(fhir_servers.save, body.model_dump(), str(identifier))


@app.delete('/api/fhir-targets/{identifier}', status_code=204)
def delete_fhir_target(identifier: UUID):
    configuration_call(fhir_servers.delete, str(identifier))


@app.post('/api/fhir-targets/{identifier}/check')
def check_fhir_target(identifier: str):
    return configuration_call(fhir_servers.check, identifier)


class DiscoveryRequest(BaseModel):
    issuer: str = Field(max_length=2048)


@app.post('/api/fhir-discovery')
def discover_fhir_provider(body: DiscoveryRequest):
    return configuration_call(fhir_servers.discover, body.issuer)


@app.post('/api/fhir-targets/{identifier}/login')
def login_fhir_target(identifier: UUID, request: Request):
    redirect = str(request.base_url).rstrip('/') + '/api/fhir-oauth/callback/' + str(identifier)
    url, binding = configuration_call(fhir_servers.begin_login, str(identifier), redirect)
    from fastapi.responses import JSONResponse
    response = JSONResponse({'url': url})
    response.set_cookie('fhir_oauth', binding, max_age=600, httponly=True, samesite='lax', secure=request.url.scheme == 'https', path='/api/fhir-oauth')
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/fhir-oauth/callback/{identifier}')
def fhir_oauth_callback(identifier: UUID, request: Request, state: str = '', code: str = '', iss: str | None = None):
    try:
        fhir_servers.complete_login(state, code, request.cookies.get('fhir_oauth'), iss, str(identifier))
        result = 'success'
    except ValueError as error:
        result = str(error)
    response = RedirectResponse('/?fhirLogin=' + quote(result, safe=''), status_code=303)
    response.delete_cookie('fhir_oauth', path='/api/fhir-oauth')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.get('/api/fhir-targets/{identifier}/resources')
def fhir_resources(identifier: str, path: str = Query(default='Patient', max_length=100), query: str = Query(default='_count=20', max_length=8192)):
    result = configuration_call(fhir_servers.request, identifier, path, query)
    # Raw upstream links are never followed with credentials. The browser submits only a relative query.
    from fastapi.responses import JSONResponse
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


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


from resource_additions import Settings as AdditionSettings


class AdditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    settings: AdditionSettings


@app.get('/api/jobs/{job_id}/additions')
def addition_catalogue(job_id: UUID):
    import resource_additions
    return configuration_call(resource_additions.catalogue, str(job_id))


@app.post('/api/jobs/{job_id}/additions', status_code=201)
def add_resources(job_id: UUID, request: AdditionRequest):
    import resource_additions
    result = configuration_call(resource_additions.create, str(job_id), request.settings,
                                request.fingerprint, str(request.requestId))
    return store.get(result)
