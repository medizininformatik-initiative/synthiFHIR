import { useState } from 'react'
import { Button } from './components/ui/button'
import type { Language } from './i18n'
import { serverMessage, serverRequest, type FhirTarget } from './fhir-servers'

const empty = { name: '', address: '', auth: 'none', username: '', password: '', token: '', flow: 'client_credentials', issuer: '', tokenUrl: '', authorizationUrl: '', clientId: '', clientSecret: '', scope: '' }
type Form = typeof empty
export function FhirServers({ language, targets, target, onSelect, onChange }: { language: Language; targets: FhirTarget[]; target: string; onSelect: (id: string) => void; onChange: () => Promise<void> }) {
  const de = language === 'de'
  const [form, setForm] = useState<Form | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [savedSecrets, setSavedSecrets] = useState<FhirTarget | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const selected = targets.find(server => server.id === target)
  const needsStart = selected && !selected.external && selected.status?.code === 'unreachable' && ['blaze', 'hapi'].includes(selected.id)
  const startCommand = needsStart ? `docker compose -f web/compose.yml --profile ${selected.id} up -d ${selected.id}-proxy` : ''
  const text = (a: string, b: string) => de ? a : b
  const field = (key: keyof Form, label: string, help: string, secret = false) => <label className="block space-y-1 text-sm" key={key}>
    <span className="font-medium">{label}</span><input className="block w-full rounded-lg border border-slate-300 p-2" type={secret ? 'password' : 'text'} autoComplete={secret ? 'new-password' : 'off'} value={form?.[key] ?? ''} onChange={e => setForm(previous => previous && { ...previous, [key]: e.target.value })}/>
    <span className="block text-xs text-slate-600">{help}</span>
  </label>
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await work() } catch (error) { setError(serverMessage(error instanceof Error ? error.message : 'server_error', language)) } finally { setBusy(false) } }
  async function save() {
    if (!form) return
    await action(async () => { const result = await serverRequest('fhir-targets' + (editing ? '/' + editing : ''), form, editing ? 'PUT' : 'POST'); onSelect(result.id); setForm(null); setSavedSecrets(null); await onChange(); setNotice(text('Verbindung gespeichert. Jetzt prüfen oder anmelden.', 'Connection saved. You can now check it or sign in.')) })
  }
  async function check() { await action(async () => { await serverRequest(`fhir-targets/${target}/check`); await onChange() }) }
  const identity: (keyof Form)[] = ['address', 'auth', ...(form?.auth === 'basic' ? ['username'] as const : form?.auth === 'oauth2' ? ['flow', 'clientId', 'tokenUrl', 'authorizationUrl', 'issuer'] as const : [])]
  const canKeepSecret = !!savedSecrets && !!form && identity.every(key => form[key] === ((savedSecrets as unknown as Record<string, unknown>)[key] ?? ''))
  const secretHelp = (saved: boolean | undefined, regular: string) => saved ? text('Gespeichert. Leer lassen, um den Wert beizubehalten.', 'Saved. Leave blank to keep the current value.') : regular
  return <div className="space-y-4">
    <div className="flex flex-wrap items-end gap-3">
      <label className="min-w-0 flex-1 text-sm font-medium">{text('FHIR-Server', 'FHIR server')}<select className="mt-1 block w-full rounded-lg border border-slate-300 p-2" value={target} disabled={busy} onChange={e => { setForm(null); setSavedSecrets(null); onSelect(e.target.value); setError(''); setNotice('') }}><option value="">{text('Server auswählen', 'Choose a server')}</option>{targets.map(server => <option key={server.id} value={server.id}>{server.name} · {server.external ? text('Extern', 'External') : text('Lokal', 'Local')}</option>)}</select></label>
      <Button variant="outline" disabled={busy} onClick={() => { setForm({ ...empty }); setEditing(null); setSavedSecrets(null); setError(''); setNotice('') }}>{text('Server hinzufügen', 'Add server')}</Button>
    </div>
    <p className="text-xs text-slate-600">{text('Diese Auswahl gilt für Upload und Ressourcenansicht.', 'This selection is used for uploads and the resource view.')}</p>
    {selected && <div className={`space-y-2 rounded-lg p-3 text-sm ${needsStart ? 'border border-amber-300 bg-amber-50' : 'bg-slate-50'}`}>
      <p className="font-medium">{selected.name}</p><p className="break-all">{selected.address}</p>
      {needsStart ? <div role="status" className="space-y-2 text-amber-950">
        <p className="font-semibold">{text(`${selected.name} ist nicht erreichbar. Starte zuerst den Server, bevor du Daten hochlädst.`, `${selected.name} is unreachable. Start the server before uploading data.`)}</p>
        <p>{text('Führe diesen Befehl im Projektverzeichnis aus:', 'Run this command from the project directory:')}</p>
        <div className="flex flex-wrap items-center gap-2"><code className="break-all rounded border border-amber-200 bg-white p-2">{startCommand}</code><Button variant="outline" disabled={busy} onClick={() => void action(async () => { await navigator.clipboard.writeText(startCommand); setNotice(text('Startbefehl kopiert.', 'Start command copied.')) })}>{text('Startbefehl kopieren', 'Copy start command')}</Button></div>
        <p>{text('Die Anzeige aktualisiert sich automatisch. Sobald der Server bereit ist, kannst du die ausgewählten Datensätze hochladen.', 'Status updates automatically. Once the server is ready, you can upload the selected datasets.')}</p>
      </div> : <p role="status">{selected.status ? serverMessage(selected.status.code, language) : text('Verbindung noch nicht geprüft.', 'Connection not checked yet.')}</p>}
      {selected.auth === 'oauth2' && selected.flow === 'authorization_code' && <div className="space-y-1 text-xs text-slate-600"><p>{text('Diese Rücksprungadresse beim Anbieter eintragen:', 'Register this redirect URI with the provider:')}</p><code className="block break-all">{window.location.origin}/api/fhir-oauth/callback/{selected.id}</code><Button variant="outline" disabled={busy} onClick={() => void action(async () => { await navigator.clipboard.writeText(`${window.location.origin}/api/fhir-oauth/callback/${selected.id}`); setNotice(text('Rücksprungadresse kopiert.', 'Redirect URI copied.')) })}>{text('Adresse kopieren', 'Copy address')}</Button></div>}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy} onClick={() => void check()}>{text('Verbindung prüfen', 'Check connection')}</Button>
        {selected.auth === 'oauth2' && selected.flow === 'authorization_code' && <Button disabled={busy} onClick={() => void action(async () => { sessionStorage.setItem('fhir-selected', target); const result = await serverRequest(`fhir-targets/${target}/login`); window.location.assign(result.url) })}>{text('Im Browser anmelden', 'Sign in through browser')}</Button>}
        {selected.external && <><Button variant="outline" disabled={busy} onClick={() => { const values = { ...empty }; for (const key of Object.keys(values) as (keyof Form)[]) { const value = (selected as unknown as Record<string, unknown>)[key]; if (typeof value === 'string') values[key] = value } setForm(values); setEditing(selected.id); setSavedSecrets(selected); setError('') }}>{text('Bearbeiten', 'Edit')}</Button>
        <Button variant="outline" disabled={busy} onClick={() => { if (window.confirm(text(`Verbindung „${selected.name}“ entfernen? Die Daten auf dem Server bleiben erhalten.`, `Remove connection “${selected.name}”? Data on the server is retained.`))) void action(async () => { await serverRequest(`fhir-targets/${target}`, undefined, 'DELETE'); onSelect(''); await onChange() }) }}>{text('Entfernen', 'Remove')}</Button></>}
      </div>
      <p className="text-xs text-slate-600">{text('Prüft Erreichbarkeit, Anmeldung und unterstützte FHIR-Funktionen. Es werden keine Daten hochgeladen. Schreibrechte werden erst beim Upload geprüft.', 'Checks connectivity, sign-in and supported FHIR features. No data is uploaded. Write permissions are checked when uploading.')}</p>
    </div>}
    {form && <fieldset disabled={busy} className="space-y-4 rounded-xl border border-teal-300 p-4"><legend className="px-2 font-semibold">{editing ? text('Verbindung bearbeiten', 'Edit connection') : text('Server hinzufügen', 'Add server')}</legend>
      {field('name', text('Name', 'Name'), text('Ein eigener Name, zum Beispiel Testserver Klinik.', 'Your own name, for example Clinic test server.'))}
      {field('address', text('FHIR-Adresse', 'FHIR address'), text('Basisadresse des FHIR-Servers, zum Beispiel https://server.example/fhir.', 'FHIR server base address, for example https://server.example/fhir.'))}
      <label className="block text-sm font-medium">{text('Anmeldung', 'Authentication')}<select className="mt-1 block w-full rounded-lg border border-slate-300 p-2" value={form.auth} onChange={e => setForm({ ...form, auth: e.target.value, password: '', token: '', clientSecret: '' })}>
        <option value="none">{text('Ohne Anmeldung', 'No authentication')}</option><option value="basic">{text('Benutzername und Passwort (Basic Auth)', 'Username and password (Basic Auth)')}</option><option value="bearer">{text('Bearer-Token', 'Bearer token')}</option><option value="oauth2">OAuth2</option>
      </select></label>
      {form.auth === 'none' && <p className="text-xs text-slate-600">{text('Verbindet sich ohne Zugangsdaten.', 'Connects without credentials.')}</p>}
      {form.auth === 'basic' && <>{field('username', text('Benutzername', 'Username'), text('Benutzername für diesen Server.', 'Username for this server.'))}{field('password', text('Passwort', 'Password'), secretHelp(canKeepSecret && savedSecrets?.hasPassword, text('Passwort für diesen Server.', 'Password for this server.')), true)}</>}
      {form.auth === 'bearer' && field('token', 'Token', secretHelp(canKeepSecret && savedSecrets?.hasToken, text('Nur den Token einfügen, ohne „Bearer“ davor.', 'Paste only the token, without “Bearer”.')), true)}
      {form.auth === 'oauth2' && <>
        <label className="block text-sm font-medium">{text('Zugangsart', 'Access type')}<select className="mt-1 block w-full rounded-lg border border-slate-300 p-2" value={form.flow} onChange={e => setForm({ ...form, flow: e.target.value })}><option value="client_credentials">{text('Technischer Zugang', 'Application access')}</option><option value="authorization_code">{text('Im Browser anmelden', 'Browser sign-in')}</option></select></label>
        <p className="text-xs text-slate-600">{form.flow === 'client_credentials' ? text('Die Workbench meldet sich als registrierte Anwendung an und erneuert Tokens automatisch.', 'The Workbench signs in as a registered application and renews tokens automatically.') : text('Nach dem Speichern melden Sie sich beim Anbieter des Servers an.', 'After saving, sign in with the server provider.')}</p>
        {field('issuer', text('Anbieteradresse (optional)', 'Provider address (optional)'), text('Adresse des OAuth2-Anbieters. Daraus lassen sich die Anmeldeadressen ermitteln.', 'OAuth2 provider address. It can be used to discover the sign-in endpoints.'))}
        <Button variant="outline" disabled={!form.issuer} onClick={() => void action(async () => { const result = await serverRequest('fhir-discovery', { issuer: form.issuer }); setForm(previous => previous && { ...previous, ...result }); setNotice(text('Anmeldeadressen übernommen.', 'Sign-in endpoints discovered.')) })}>{text('Anmeldeadressen ermitteln', 'Discover endpoints')}</Button>
        {field('tokenUrl', text('Token-Adresse', 'Token endpoint'), text('Adresse, unter der der Anbieter Zugangstokens ausstellt.', 'Address where the provider issues access tokens.'))}
        {form.flow === 'authorization_code' && field('authorizationUrl', text('Anmeldeadresse', 'Authorization endpoint'), text('Adresse für die Anmeldung im Browser.', 'Address for browser sign-in.'))}
        {field('clientId', 'Client-ID', text('Kennung der beim Anbieter registrierten Anwendung.', 'Identifier of the application registered with the provider.'))}
        {field('clientSecret', form.flow === 'authorization_code' ? 'Client-Secret (optional)' : 'Client-Secret', secretHelp(canKeepSecret && savedSecrets?.hasClientSecret, text('Geheimnis der registrierten Anwendung.', 'Secret of the registered application.')), true)}
        {field('scope', text('Berechtigungen (Scopes, optional)', 'Permissions (scopes, optional)'), text('Vom Anbieter vorgegebene Berechtigungen, durch Leerzeichen getrennt.', 'Permissions specified by the provider, separated by spaces.'))}
        {form.flow === 'authorization_code' && !editing && <p className="text-xs text-slate-600">{text('Nach dem Speichern erscheint die Rücksprungadresse. Diese beim Anbieter eintragen und danach im Browser anmelden.', 'The redirect URI appears after saving. Register it with the provider, then sign in through the browser.')}</p>}
      </>}
      <div className="flex gap-2"><Button onClick={() => void save()}>{text('Speichern', 'Save')}</Button><Button variant="outline" onClick={() => { setForm(null); setError('') }}>{text('Abbrechen', 'Cancel')}</Button></div>
    </fieldset>}
    {busy && <p role="status" className="text-sm">{text('Verbindung wird bearbeitet …', 'Working on connection …')}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
  </div>
}
