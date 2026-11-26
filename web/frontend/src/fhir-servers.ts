import type { Language } from './i18n'
export type FhirTarget = { id: string; revision?: number; name: string; address: string; url?: string; available: boolean; external?: boolean; auth?: string; flow?: string; status?: { code: string; read: boolean; upload: boolean; resourceTypes?: string[] }; hasPassword?: boolean; hasToken?: boolean; hasClientSecret?: boolean }
const messages: Record<string, [string, string]> = {
  ready: ['FHIR R4 erkannt · Transaktionen unterstützt.', 'FHIR R4 detected · Transactions supported.'],
  read_only: ['FHIR R4 erkannt · Upload nicht unterstützt.', 'FHIR R4 detected · Upload not supported.'],
  unreachable: ['Server nicht erreichbar. Adresse und Netzwerkverbindung prüfen.', 'Server unreachable. Check its address and network connection.'],
  login_rejected: ['Anmeldung abgelehnt. Zugangsdaten prüfen.', 'Sign-in rejected. Check the credentials.'],
  forbidden: ['Die Berechtigung für diese Aktion fehlt.', 'Permission for this action is missing.'],
  login_required: ['Bitte zuerst im Browser anmelden.', 'Please sign in through the browser first.'],
  login_expired: ['Anmeldung abgelaufen. Bitte erneut anmelden.', 'Sign-in expired. Please sign in again.'],
  oauth_failed: ['OAuth2-Anmeldung fehlgeschlagen. Zugangsdaten prüfen oder erneut anmelden.', 'OAuth2 sign-in failed. Check the credentials or sign in again.'],
  certificate_error: ['Das Serverzertifikat konnte nicht bestätigt werden.', 'The server certificate could not be verified.'],
  unsupported_version: ['Dieser Server verwendet eine andere FHIR-Version. Benötigt wird FHIR R4.', 'This server uses a different FHIR version. FHIR R4 is required.'],
  invalid_response: ['Die Serverantwort konnte nicht als FHIR- oder Anmeldeantwort gelesen werden.', 'The server response could not be read as a FHIR or sign-in response.'],
  redirect_blocked: ['Der Server leitet die Anfrage um. Bitte die direkte Adresse eintragen.', 'The server redirects requests. Please enter its direct address.'],
  invalid_url: ['Eine vollständige HTTP- oder HTTPS-Adresse ohne Abfrage oder Zugangsdaten eingeben.', 'Enter a complete HTTP or HTTPS address without query parameters or credentials.'],
  https_required: ['Für Zugangsdaten benötigt der externe Server eine HTTPS-Adresse.', 'External servers need an HTTPS address when credentials are used.'],
  invalid_fields: ['Bitte die eingegebenen Felder prüfen.', 'Please check the entered fields.'],
  missing_fields: ['Bitte alle benötigten Felder ausfüllen.', 'Please fill in all required fields.'],
  invalid_token: ['Den Token ohne Leerzeichen und ohne „Bearer“ einfügen.', 'Paste the token without spaces or “Bearer”.'],
  server_busy: ['Für diesen Server läuft ein Upload. Änderungen sind danach möglich.', 'An upload to this server is running. Changes are possible afterwards.'],
  server_missing: ['Diese Verbindung wurde entfernt. Bitte einen Server auswählen.', 'This connection was removed. Please select a server.'],
  server_changed: ['Die Serververbindung wurde geändert. Bitte einen neuen Upload starten.', 'The connection changed. Please start a new upload.'],
  not_found: ['Die angefragte Adresse oder Ressource wurde nicht gefunden.', 'The requested address or resource was not found.'],
  invalid_query: ['Ressourcentyp oder Suchparameter prüfen.', 'Check the resource type or search parameters.'],
  response_too_large: ['Die Antwort ist zu groß. Die Suche bitte weiter eingrenzen.', 'The response is too large. Please narrow the search.'],
  server_error: ['Der Server konnte die Anfrage nicht ausführen.', 'The server could not complete the request.'],
}
export function serverMessage(code: string, language: Language) { return (messages[code] ?? messages.server_error)[language === 'de' ? 0 : 1] }
export function uploadError(message: string, language: Language) { return messages[message] ? serverMessage(message, language) : message }
export async function serverRequest(path: string, body?: unknown, method = 'POST') {
  const response = await fetch('/api/' + path, { method, headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  if (!response.ok) { const data = await response.json().catch(() => null); throw new Error(typeof data?.detail === 'string' ? data.detail : 'server_error') }
  return response.status === 204 ? null : response.json()
}
export function nextResourceQuery(link: string, base: string): { path: string; query: string } | null {
  try {
    const root = new URL(base.replace(/\/$/, '') + '/')
    const next = new URL(link, root)
    const atRoot = next.pathname === root.pathname.slice(0, -1)
    if (next.origin !== root.origin || (!atRoot && !next.pathname.startsWith(root.pathname)) || next.hash || next.username || next.password) return null
    const path = atRoot ? '' : next.pathname.slice(root.pathname.length)
    if (path && !/^[A-Z][A-Za-z0-9]*(\/[A-Za-z0-9.-]{1,64})?$/.test(path)) return null
    return { path, query: next.search.slice(1) }
  } catch { return null }
}
