import { BulkOutputSettings } from './BulkOutputSettings'
import identifierBindings from '../../catalog/options/identifier-bindings.json'
import { SavedConfigurations } from './SavedConfigurations'
import { exportPropertiesConfiguration } from './configuration-properties'
import { useEffect, useRef, useState } from 'react'
import { Plus, Trash2, Download, Upload, RotateCcw, Check } from 'lucide-react'
import { Button } from './components/ui/button'
import { Help } from './Help'
import { IdentifierPatternControl } from './IdentifierPatternControl'
import { translate, type Language, type TextKey, type Message } from './i18n'
import {
  contract,
  options,
  darFields,
  darResource,
  defaults,
  importConfiguration,
  restoreBrowserDraft,
  problems,
  optionEnabled,
  unmetDependencies,
  resourceEnabled,
  resourceNavigationStatus,
  resourceOptions,
  previewIdentifier,
  identifierNumber,
  type Configuration,
  type Option,
  type Value,
  type Rule
} from './configuration'

const storageKey = 'workbenchConfiguration.v1'
const groups = [
  { id: 'demographics', key: 'app.config.group.demographics' },
  { id: 'contacts', key: 'app.config.group.contacts' },
  { id: 'clinical', key: 'app.config.group.clinical' },
  { id: 'medication', key: 'app.config.group.medication' }
]
const resources = groups.flatMap((group) =>
  contract.resources.filter((r) => r.group === group.id)
)
const identifierChoices = [...new Map(resources.filter(r => r.identifierEligible).map(r => [
  r.identifierSelector ?? r.resourceType, r
])).entries()].flatMap(([selector, r]) => [
  { selector, resourceType: r.resourceType, labelKey: selector === 'Encounter' ? 'app.config.encounterAll'
    : selector === 'Observation' ? 'app.config.observation' : r.labelKey,
    classCode: 'classCode' in r ? r.classCode : '', level: '', category: '' },
  ...contract.identifierScopes.filter(scope => selector === 'Encounter.inpatient'
    ? scope.selector.startsWith(selector + '.') : selector === 'Observation' && scope.resourceType === selector)
])
const darGroups = resources.map((resource) => ({
  ...resource,
  filter: resource.id === 'Observation.laboratory' ? 'Laboratory'
    : resource.id === 'Observation.vitalSigns' ? 'VitalSigns' : resource.classCode ? resource.id : resource.resourceType,
  fields: darFields.filter((field) => darResource(field) === (
    resource.id === 'Observation.laboratory' ? 'Laboratory'
      : resource.id === 'Observation.vitalSigns' ? 'VitalSigns' : resource.classCode ? resource.id : resource.resourceType
  ))
})).filter((group) => group.fields.length > 0)
const idGroups = [
  { prefix: 'ids.patient.', key: 'app.config.group.patientIds' },
  { prefix: 'ids.start.', key: 'app.config.group.counters' },
  { prefix: 'timeShift.', key: 'app.config.group.timeShift' }
]
const counterResources: Record<string, string> = {
  CONSENT: 'Consent', CONDITION: 'Condition',
  ENCOUNTER_LEVEL_2: 'Encounter', ENCOUNTER_LEVEL_3: 'Encounter',
  MEDICATION_REQUEST: 'MedicationRequest',
  MEDICATION_ADMINISTRATION: 'MedicationAdministration',
  MEDICATION_STATEMENT: 'MedicationStatement',
  OBSERVATION_LABORATORY: 'Observation', OBSERVATION_VITAL_SIGNS: 'Observation',
  PROCEDURE: 'Procedure', DOCUMENT_REFERENCE: 'DocumentReference'
}
type Translator = (key: string, params?: Message['params']) => string
function OptionControl({
  option,
  config,
  update,
  t,
  compact = false,
  showHelp = true,
  fhirPath = option.fhirPath
}: {
  option: Option
  config: Configuration
  update: (id: string, value: Value) => void
  t: Translator
  compact?: boolean
  showHelp?: boolean
  fhirPath?: string
}) {
  const enabled = optionEnabled(option.id, config.values)
  const value = config.values[option.id]
  const dependencies = unmetDependencies(option.enabledWhen, config.values)
  const explain = (deps: typeof dependencies) =>
    t('app.config.requires', {
      labels: deps
        .map((d) => {
          const source = options.find((o) => o.id === d.option)!
          const choice = source.choiceLabelKeys?.[String(d.equals)]
          return `${t(source.labelKey)}: ${choice ? t(choice) : t(d.equals ? 'app.config.on' : 'app.config.off')}`
        })
        .join(' · ')
    })
  return (
    <div
      className={compact ? 'option-row min-w-0 py-2' : 'option-row border-t border-slate-100 py-4 first:border-t-0'}
      data-option={option.id}
      id={option.type === 'enum' && option.control !== 'select' ? option.id : undefined}
    >
      <div className={`${option.type === 'boolean' ? '' : 'mb-2'} flex flex-wrap items-center gap-x-2 gap-y-1`}>
        <label htmlFor={option.id} className="flex items-center gap-2 font-medium text-sm">
          {option.type === 'boolean' && (
            <input
              id={option.id}
              type="checkbox"
              disabled={!enabled}
              checked={value === true}
              onChange={(e) => update(option.id, e.target.checked)}
              className="h-4 w-4 shrink-0 accent-teal-700"
            />
          )}
          {t(option.labelKey)}
        </label>
        <span className="inline-flex max-w-full items-center gap-2">
          {fhirPath && (
            <span className="rounded border border-slate-200 bg-slate-100 px-2 py-1 text-xs font-normal text-slate-600">
              {t('app.config.fhirResource', { resource: fhirPath })}
            </span>
          )}
          {showHelp && <Help text={t(option.helpKey)} t={t} />}
        </span>
      </div>
      {option.type === 'boolean' ? null : option.control === 'select' ? (
        <select id={option.id} disabled={!enabled} value={String(value)}
          onChange={(e) => update(option.id, e.target.value)}
          className="w-full max-w-sm rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100">
          {option.choices?.map((choice) => <option key={String(choice)} value={String(choice)}>{t(option.choiceLabelKeys![String(choice)])}</option>)}
        </select>
      ) : option.type === 'enum' || option.type === 'set' ? (
        <div
          role="group"
          aria-label={t(option.labelKey)}
          className={option.id === 'reference.DocumentReference.assignmentStrategy'
            ? 'flex flex-col items-start gap-3'
            : 'flex flex-wrap gap-x-5 gap-y-3'}
        >
          {option.choices?.map((choice) => {
            const unmet = unmetDependencies(
              option.choiceDependencies?.[String(choice)],
              config.values
            )
            const available = enabled && !unmet.length
            const label = t(option.choiceLabelKeys![String(choice)])
            return (
              <div key={String(choice)} className="text-sm">
                <label
                  className={`flex items-center gap-2 ${available ? '' : 'text-slate-400'}`}
                >
                  <input
                    type={option.type === 'set' ? 'checkbox' : 'radio'}
                    name={option.id}
                    disabled={!available}
                    checked={
                      option.type === 'set'
                        ? Array.isArray(value) && value.includes(String(choice))
                        : value === choice
                    }
                    onChange={(e) =>
                      update(
                        option.id,
                        option.type === 'set'
                          ? e.target.checked
                            ? [...(value as string[]), String(choice)]
                            : (value as string[]).filter((v) => v !== choice)
                          : choice
                      )
                    }
                    className="h-4 w-4 accent-teal-700"
                  />
                  {label}
                </label>
                {enabled && unmet.length > 0 && (
                  <p className="mt-1 max-w-64 text-xs text-slate-500">
                    {explain(unmet)}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <input
          id={option.id}
          type={option.type === 'integer' ? 'number' : 'text'}
          step={option.type === 'integer' ? 1 : undefined}
          min={option.minimum}
          disabled={!enabled}
          value={
            typeof value === 'string' || typeof value === 'number' ? value : ''
          }
          onChange={(e) =>
            update(
              option.id,
              option.type === 'integer'
                ? e.target.value === ''
                  ? null
                  : Number(e.target.value)
                : e.target.value
            )
          }
          className="w-full max-w-sm rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100 disabled:text-slate-400"
        />
      )}
      {!enabled && (
        <p className="mt-2 text-xs text-slate-500">{dependencies.length ? explain(dependencies) : t('app.config.endApplicationInactive')}</p>
      )}
    </div>
  )
}
function RulePreview({ rule, t }: { rule: Rule; t: Translator }) {
  const [preview, setPreview] = useState('')
  useEffect(() => {
    let active = true
    setPreview('')
    void previewIdentifier(rule)
      .then((text) => {
        if (active) setPreview(text)
      })
      .catch(() => {
        if (active) setPreview(t('app.config.previewInvalid'))
      })
    return () => {
      active = false
    }
  }, [rule, t])
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg bg-slate-50 px-3 py-2">
      <div className="flex items-center gap-1">
        <p className="text-xs text-slate-500">{t('app.config.previewLabel')}</p>
        <Help text={t('app.config.previewHint')} t={t} />
      </div>
      <output className="block break-all font-mono text-sm">
        {preview}
      </output>
    </div>
  )
}
export function ConfigurationEditor({ language, onChange, initialConfiguration, bulkGzip = true, onBulkGzipChange, bulkMode = false, readOnly = false }: { bulkGzip?: boolean; onBulkGzipChange?: (value: boolean) => void; readOnly?: boolean; bulkMode?: boolean; language: Language; onChange?: (config: Configuration) => void; initialConfiguration?: Configuration }) {
  const t: Translator = (key, params) =>
    translate(language, key as TextKey, params)
  const [initial] = useState(() => {
    if (initialConfiguration) return { config: initialConfiguration, failed: false, saved: false }
    try {
      const saved = localStorage.getItem(storageKey)
      return {
        config: saved ? restoreBrowserDraft(saved) : defaults(),
        failed: false,
        saved: Boolean(saved)
      }
    } catch {
      return { config: defaults(), failed: true, saved: false }
    }
  })
  const [config, setConfig] = useState<Configuration>(initial.config)
  useEffect(() => {
    if (bulkMode && (config.values['ids.patient.additionalRepetitions'] !== 0 || config.values['timeShift.iterationDays'] !== 0)) {
      setConfig(old => ({ ...old, values: { ...old.values, 'ids.patient.additionalRepetitions': 0, 'timeShift.iterationDays': 0 } }))
      return
    }
    onChange?.(config)
  }, [config, onChange, bulkMode])
  const [tab, setTab] = useState('resources')
  const [message, setMessage] = useState<Message | null>(
    initial.failed ? { key: 'app.config.restoreFailed' } : null
  )
  const [dirty, setDirty] = useState(Boolean(initialConfiguration))
  const [saved, setSaved] = useState(initial.saved)
  const [storageFailed, setStorageFailed] = useState(false)
  useEffect(() => {
    if (!dirty) return
    setStorageFailed(false)
    const persist = () => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(config))
        setDirty(false)
        setSaved(true)
        setStorageFailed(false)
      } catch {
        setStorageFailed(true)
      }
    }
    const timer = window.setTimeout(persist, 400)
    window.addEventListener('pagehide', persist)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('pagehide', persist)
    }
  }, [config, dirty])
  const [darCodes, setDarCodes] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(config.dar).flatMap(([id, rule]) =>
        rule.mode === 'overwrite' ? [[id, rule.code]] : []
      )
    )
  )
  const [darFilter, setDarFilter] = useState('all')
  const importRef = useRef<HTMLInputElement>(null)
  const issues = problems(config)
  const update = (id: string, value: Value) => {
    setConfig((c) => ({ ...c, values: { ...c.values, [id]: value } }))
    setDirty(true)
    setMessage(null)
  }
  const updateRule = (id: string, update: Partial<Rule>) => {
    setConfig((c) => ({
      ...c,
      identifierRules: c.identifierRules.map((rule) =>
        rule.id === id ? { ...rule, ...update } : rule
      )
    }))
    setDirty(true)
    setMessage(null)
  }
  async function load(file?: File) {
    if (!file) return
    try {
      const next = importConfiguration(await file.text())
      setConfig(next)
      setDarCodes(
        Object.fromEntries(
          Object.entries(next.dar).flatMap(([id, value]) =>
            value.mode === 'overwrite' ? [[id, value.code]] : []
          )
        )
      )
      setDirty(true)
      setMessage({ key: 'app.config.imported' })
    } catch {
      setMessage({ key: 'app.config.importFailed' })
    }
    if (importRef.current) importRef.current.value = ''
  }
  const exportHref =
    'data:text/plain;charset=utf-8,' +
    encodeURIComponent(issues.length ? '' : exportPropertiesConfiguration(config, language))
  return (
    <section
      className="mt-8 rounded-2xl border border-slate-200 bg-white shadow-sm"
      aria-label={t('app.config.title')}
    >
      <div className="p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold">
              {t('app.config.title')}
            </h2>
          </div>
          <span role="status" className={`rounded-full px-3 py-1 text-xs ${storageFailed ? 'bg-red-50 text-red-700' : 'bg-slate-100 text-slate-600'}`}>
            {t(storageFailed ? 'app.config.storageError' : dirty ? 'app.config.saving' : saved ? 'app.config.saved' : 'app.config.ready')}
          </span>
        </div>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
          {t('app.config.integrationHint')}
        </p>
        <fieldset disabled={readOnly}><SavedConfigurations editorActions={<>
          {issues.length > 0 ? (
            <Button variant="outline" disabled>
              <Download size={15} />
              {t('app.config.export')}
            </Button>
          ) : (
            <Button asChild variant="outline">
              <a href={exportHref} download="converter-configuration.config">
                <Download size={15} />
                {t('app.config.export')}
              </a>
            </Button>
          )}
          <Button variant="outline" onClick={() => importRef.current?.click()}>
            <Upload size={15} />
            {t('app.config.import')}
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setConfig(defaults())
              setDarCodes({})
              setDirty(true)
              setMessage(null)
            }}
          >
            <RotateCcw size={15} />
            {t('app.config.reset')}
          </Button>
          <input
            ref={importRef}
            type="file"
            accept="text/plain,.config,application/json,.json"
            aria-label={t('app.config.import')}
            hidden
            onChange={(e) => void load(e.target.files?.[0])}
          />
        </>} language={language} configuration={config} valid={issues.length === 0} onLoad={next => {
          setConfig(next)
          setDarCodes(Object.fromEntries(Object.entries(next.dar).flatMap(([id, value]) => value.mode === 'overwrite' ? [[id, value.code]] : [])))
          setDirty(true)
          setMessage(null)
        }}/>
        </fieldset>
        {message && (
          <p role="status" className="mt-4 text-sm">
            {t(message.key, message.params)}
          </p>
        )}
        {issues.length > 0 && (
          <div
            role="alert"
            className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-800"
          >
            {issues.map((issue, i) => (
              <p key={i}>{t(issue.key, issue.params)}</p>
            ))}
          </div>
        )}
      </div>
      <h3 className="px-6 pb-3 text-xs font-semibold uppercase tracking-widest text-teal-700">
        {t('app.config.editor')}
      </h3>
      <div
        role="tablist"
        aria-label={t('app.config.title')}
        className="flex flex-wrap gap-2 border-y border-slate-200 bg-slate-100 p-3"
      >
        {contract.sections.map((section, index) => (
          <button
            key={section.id}
            id={`tab-${section.id}`}
            role="tab"
            aria-selected={tab === section.id}
            aria-controls={`panel-${section.id}`}
            tabIndex={tab === section.id ? 0 : -1}
            onClick={() => setTab(section.id)}
            onKeyDown={(e) => {
              let next = index
              if (e.key === 'ArrowRight')
                next = (index + 1) % contract.sections.length
              else if (e.key === 'ArrowLeft')
                next =
                  (index - 1 + contract.sections.length) %
                  contract.sections.length
              else if (e.key === 'Home') next = 0
              else if (e.key === 'End') next = contract.sections.length - 1
              else return
              e.preventDefault()
              setTab(contract.sections[next].id)
              document
                .getElementById(`tab-${contract.sections[next].id}`)
                ?.focus()
            }}
            className={`rounded-lg border px-3 py-2 text-sm font-medium shadow-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 ${tab === section.id ? 'border-teal-800 bg-teal-800 text-white' : 'border-slate-300 bg-white text-slate-700 hover:border-teal-700 hover:bg-teal-50 hover:text-teal-900'}`}
          >
            {t(section.labelKey)}
          </button>
        ))}
      </div>
      <div
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        className="p-6"
      >
        <fieldset disabled={readOnly}>
        <header className="mb-6">
          <h3 className="mb-3 text-lg font-semibold">{t(`section.${tab}`)}</h3>
          <p className="max-w-3xl text-sm text-slate-600">
            {t(tab === 'output' && bulkMode ? 'app.bulk.outputIntro' : ({ resources: 'app.config.resourcesIntro', identifiers: 'identifier.help', dar: 'app.config.darHint', ids: 'app.config.idsIntro', terminology: 'app.config.terminologyIntro', output: 'app.config.outputIntro' } as Record<string, string>)[tab])}
          </p>
        </header>
        {tab === 'resources' && (
          <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-start gap-4">
            <nav
              aria-label={t('app.config.anchors')}
              className="sticky top-4 max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-y-contain [scrollbar-gutter:stable]"
            >
              <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {t('app.config.anchors')}
              </p>
              <div className="flex w-max flex-col items-stretch gap-2 pb-16">
                {resources.flatMap(r => [
                  { id: r.id, labelKey: r.labelKey, organization: false },
                  ...(r.id === 'Encounter' ? [{ id: 'Organization', labelKey: 'app.config.organization', organization: true }] : [])
                ]).map((r) => {
                  const status = resourceNavigationStatus(r.id, config.values)
                  const round = r.organization || `resource.${r.id}.mode` in config.values
                  const active = status === 'generated' || status === 'referenced'
                  const modeOption = options.find((option) => option.id === (r.organization ? 'resource.Encounter.stationServiceProvider' : `resource.${r.id}.mode`))
                  const selectedLabel = status !== 'parentDisabled' ? modeOption?.choiceLabelKeys?.[String(config.values[modeOption.id])] : undefined
                  const description = t(selectedLabel ?? {
                    generated: 'app.config.resourceStatus.generated',
                    referenced: 'app.config.resourceStatus.referenced',
                    disabled: 'app.config.resourceStatus.disabled',
                    parentDisabled: 'app.config.resourceStatus.parentDisabled'
                  }[status])
                  return (
                    <a key={r.id} href={r.organization ? '#resource.Encounter.stationServiceProvider' : `#resource-${r.id}`}
                      aria-describedby={`resource-status-${r.id}`}
                      className={`group relative flex items-center gap-2 rounded px-2 py-1 text-teal-800 hover:bg-teal-50 focus-visible:outline-2 focus-visible:outline-teal-700 ${r.organization ? 'ml-5 text-xs' : 'text-sm'}`}>
                      <span className="group/status relative h-2.5 w-2.5 shrink-0">
                        <span aria-hidden="true" className={`flex h-2.5 w-2.5 items-center justify-center border ${round ? 'rounded-full' : 'rounded-sm'} ${status === 'generated' ? 'border-teal-700 bg-teal-700' : status === 'referenced' ? 'border-teal-700' : 'border-slate-400 bg-slate-100'}`}>
                          {round && status === 'referenced' && <span className="h-1 w-1 rounded-full bg-teal-700" />}
                          {!round && active && <Check className="h-2 w-2 text-white" strokeWidth={3} />}
                        </span>
                        <span role="tooltip" id={`resource-status-${r.id}`}
                          className="pointer-events-none absolute left-0 top-full z-20 hidden w-36 rounded bg-slate-800 px-3 py-2 text-xs text-white shadow-lg group-hover/status:block group-focus-visible:block">
                          {description}
                        </span>
                      </span>
                      <span className="whitespace-nowrap">{t(r.labelKey)}</span>
                    </a>
                  )
                })}
              </div>
            </nav>
            <div>
              {resources.map((r, index) => (
                <div key={r.id}>
                  {(index === 0 || resources[index - 1].group !== r.group) && (
                    <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-teal-800">
                      {t(groups.find((g) => g.id === r.group)!.key)}
                    </h3>
                  )}
                  <section
                    key={r.id}
                    id={`resource-${r.id}`}
                    className="mb-6 scroll-mt-6 rounded-xl border border-slate-200 p-5"
                  >
                    <h3 className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-lg font-semibold">
                      <span className="text-xs font-normal text-slate-400">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      {t(r.labelKey)}
                      <span className="rounded border border-slate-200 bg-slate-100 px-2 py-1 text-xs font-normal text-slate-600">
                        {t('app.config.fhirResource', { resource: r.resourceType })}
                        {r.classCode && ` · ${t('app.config.encounterClass', { code: r.classCode })}`}
                        {r.id === 'Observation.vitalSigns' && ` · ${t('app.config.category')}: vital-signs`}
                        {r.id === 'Observation.laboratory' && ` · ${t('app.config.category')}: laboratory`}
                      </span>
                    </h3>
                    {r.id === 'Encounter' && <p className="mb-4 text-sm text-slate-600">{t('app.config.encounterCommon')}</p>}
                    {r.classCode && <>
                      <p className="mb-2 text-sm text-slate-600">{t('app.config.encounterEndSummary')}</p>
                    </>}
                    {resourceOptions(r.id).map((option) => (
                      <OptionControl
                        key={option.id}
                        option={option}
                        config={config}
                        update={update}
                        t={t}
                      />
                    ))}
                  </section>
                </div>
              ))}
            </div>
          </div>
        )}
        {tab === 'ids' && (
          <div className="space-y-5">
            {bulkMode && <p className="text-sm text-slate-600">{t('app.bulk.noCopies')}</p>}
            {idGroups.map((group) => (
              <section key={group.prefix} className="rounded-xl border border-slate-200 p-5" aria-label={t(group.key)}>
                <div className="mb-3 flex items-center gap-2">
                  <h3 className="text-base font-semibold text-teal-800">{t(group.key)}</h3>
                  {group.prefix === 'ids.start.' && <Help text={t('option.ids.start.CONSENT.help')} t={t} />}
                </div>
                <div className={`grid gap-x-8 gap-y-2 md:grid-cols-2 ${group.prefix === 'ids.start.' ? 'xl:grid-cols-3' : ''}`}>
                  {options.filter((o) => o.id.startsWith(group.prefix) && (!bulkMode || !['ids.patient.additionalRepetitions', 'timeShift.iterationDays'].includes(o.id))).map((option) => (
                    <div key={option.id} className={option.id === 'timeShift.enabled' || option.id === 'ids.patient.additionalRepetitions' ? 'md:col-span-2' : 'min-w-0'}>
                      <OptionControl
                        option={option}
                        config={config}
                        update={update}
                        t={t}
                        compact
                        showHelp={group.prefix !== 'ids.start.'}
                        fhirPath={option.id === 'ids.start.OBSERVATION_LABORATORY'
                          ? `Observation · ${t('app.config.category')}: laboratory`
                          : option.id === 'ids.start.OBSERVATION_VITAL_SIGNS'
                            ? `Observation · ${t('app.config.category')}: vital-signs`
                            : counterResources[option.id.replace('ids.start.', '')]}
                      />
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
        {['terminology', 'output'].includes(tab) && (
          <div className="max-w-3xl">
            {tab === 'terminology' && (
              <p className="mb-4 rounded-lg bg-teal-50 p-4 text-sm">
                {t('app.config.baseline', contract.profileBaseline)}
              </p>
            )}
            {tab === 'output' && bulkMode && <BulkOutputSettings language={language} gzip={bulkGzip} onChange={value => onBulkGzipChange?.(value)} disabled={readOnly}/>}
            {options
              .filter((o) => o.section === tab && !(tab === 'output' && bulkMode))
              .map((o) => (
                <OptionControl
                  key={o.id}
                  option={o}
                  config={config}
                  update={update}
                  t={t}
                />
              ))}
          </div>
        )}
        {tab === 'dar' && (
          <>
            <label className="mb-5 flex max-w-sm flex-col gap-2 text-sm">
              {t('app.config.filter')}
              <select
                value={darFilter}
                onChange={(e) => setDarFilter(e.target.value)}
                className="rounded-lg border border-slate-300 p-2"
              >
                <option value="all">{t('app.config.allResources')}</option>
                {darGroups.map((group) => (
                  <option key={group.id} value={group.filter}>
                    {t(group.labelKey)}
                  </option>
                ))}
              </select>
            </label>
            <div className="space-y-6">
              {darGroups.filter((group) => darFilter === 'all' || group.filter === darFilter).map((group) => (
                <section key={group.id} className="rounded-xl border border-slate-200 p-5" aria-labelledby={`dar-group-${group.id}`}>
                  <h3 id={`dar-group-${group.id}`} className="mb-3 text-lg font-semibold text-teal-800">
                    {t(group.id === 'Encounter' ? 'app.config.encounterDarTitle' : group.labelKey)}
                  </h3>
                  {group.classCode && <p className="mb-3 text-xs text-slate-600">{t('app.config.encounterClass', { code: group.classCode })}</p>}
                  {(group.id === 'Encounter' || group.classCode) && <p className="mb-4 text-sm text-slate-600">{t(group.classCode ? 'app.config.encounterDarScoped' : 'app.config.encounterDarCommon')}</p>}
                  {group.fields.map((field) => {
                  const current = config.dar[field.id] ?? { mode: 'unchanged' }
                  const enabled = resourceEnabled(
                    darResource(field),
                    config.values
                  )
                  const code =
                    current.mode === 'overwrite'
                      ? current.code
                      : (darCodes[field.id] ?? '')
                  const set = (
                    mode: 'unchanged' | 'overwrite',
                    code: string,
                    onlyWhenMissing = current.mode === 'overwrite' && (current.onlyWhenMissing ?? false)
                  ) => {
                    setConfig((c) => ({
                      ...c,
                      dar: {
                        ...c.dar,
                        [field.id]:
                          mode === 'unchanged' ? { mode } : { mode, code, onlyWhenMissing }
                      }
                    }))
                    setDirty(true)
                    setMessage(null)
                  }
                  return (
                    <div
                      key={field.id}
                      className="border-t border-slate-100 py-4"
                    >
                      <div className="mb-3 flex flex-wrap items-center gap-2">
                        <h4 className="text-sm font-medium">
                          {t(`dar.field.${field.id}.label`).replace(/^[^:]+:\s*/, '')}
                        </h4>
                        {field.targets.map((path) => (
                          <span
                            key={path}
                            className="rounded border border-slate-200 bg-slate-100 px-2 py-1 text-xs font-normal text-slate-600"
                          >
                            {t('app.config.fhirResource', { resource: path })}
                          </span>
                        ))}
                      </div>
                      <div className="flex flex-wrap gap-4">
                        <div role="radiogroup" aria-label={t(`dar.field.${field.id}.label`)} className="flex flex-wrap items-center gap-4">
                          {(['unchanged', 'missing', 'overwrite'] as const).map(mode => (
                            <label key={mode} className={`flex items-center gap-2 text-sm ${enabled ? '' : 'text-slate-400'}`}>
                              <input type="radio" name={`dar-mode-${field.id}`} disabled={!enabled}
                                checked={mode === 'unchanged' ? current.mode === 'unchanged' : current.mode === 'overwrite' && (mode === 'missing' ? !!current.onlyWhenMissing : !current.onlyWhenMissing)}
                                onChange={() => set(mode === 'unchanged' ? 'unchanged' : 'overwrite', code, mode === 'missing')}
                                className="accent-teal-700" />
                              {t(mode === 'unchanged' && group.classCode ? 'dar.mode.inherit' : mode === 'missing' ? 'dar.onlyWhenMissing' : `dar.mode.${mode}`)}
                            </label>
                          ))}
                          <Help text={t('dar.onlyWhenMissingHelp')} t={t}/>
                        </div>
                        <div className="flex min-w-0 flex-col gap-2">
                          <select
                            aria-label={`${t(`dar.field.${field.id}.label`)} – ${t('app.config.standardCode')}`}
                            value={field.allowedCodes.includes(code) ? code : ''}
                            disabled={!enabled || current.mode !== 'overwrite'}
                            onChange={(e) => {
                              setDarCodes((old) => ({ ...old, [field.id]: e.target.value }))
                              set('overwrite', e.target.value)
                            }}
                            className="max-w-full rounded-lg border border-slate-300 p-2 text-sm disabled:bg-slate-100 disabled:text-slate-400"
                          >
                            <option value="">{t('app.config.customCode')}</option>
                            {field.allowedCodes.map((c) => <option key={c} value={c}>{t(`dar.code.${c}`)}</option>)}
                          </select>
                          <input
                            aria-label={t(`dar.field.${field.id}.label`)}
                            value={code}
                            placeholder={t('app.config.chooseCode')}
                            disabled={!enabled || current.mode !== 'overwrite'}
                            onChange={(e) => {
                              setDarCodes((old) => ({ ...old, [field.id]: e.target.value }))
                              set('overwrite', e.target.value)
                            }}
                            className="max-w-full rounded-lg border border-slate-300 p-2 text-sm disabled:bg-slate-100 disabled:text-slate-400"
                          />
                        </div>
                      </div>

                      {!enabled ? (
                        <p className="mt-2 text-xs text-slate-500">
                          {t('app.config.resourceRequired')}
                        </p>
                      ) : current.mode !== 'overwrite' ? (
                        <p className="mt-2 text-xs text-slate-500">
                          {t(group.classCode ? 'app.config.darInherited' : 'disabled.darMode')}
                        </p>
                      ) : null}
                      {field.codeConditions[code] && (
                        <p className="mt-2 text-xs text-amber-800">
                          {t(
                            code === 'as-text'
                              ? 'app.config.narrativeCondition'
                              : 'app.config.notPerformedCondition'
                          )}
                        </p>
                      )}
                    </div>
                  )
                  })}
                </section>
              ))}
            </div>
          </>
        )}
        {tab === 'identifiers' && (
          <>
            <Button
              variant="outline"
              onClick={() => {
                setConfig((c) => ({
                  ...c,
                  identifierRules: [
                    ...c.identifierRules,
                    {
                      id: crypto.randomUUID(),
                      enabled: true,
                      resources: ['Patient'],
                      system: 'https://example.org/fhir/sid/patient-id',
                      pattern: '{count:08}'
                    }
                  ]
                }))
                setDirty(true)
                setMessage(null)
              }}
            >
              <Plus size={16} />
              {t('app.config.addRule')}
            </Button>
            <span className="ml-3 inline-flex items-center gap-2">
              <Button variant="outline" onClick={() => {
                setConfig(c => ({ ...c, identifierRules: [...c.identifierRules, {
                  id: crypto.randomUUID(), enabled: true, scope: 'case',
                  resources: ['Encounter'], system: 'https://example.org/fhir/sid/case-id', pattern: '{patientId}-{caseId}'
                }] }))
                setDirty(true); setMessage(null)
              }}><Plus size={16}/>{t('app.config.addCaseRule')}</Button>
              <Help text={t('app.config.caseRuleHelp')} t={t}/>
            </span>
            {config.identifierRules.length === 0 && (
              <p className="mt-6 text-sm text-slate-500">
                {t('app.config.noRules')}
              </p>
            )}
            {config.identifierRules.map((rule, index) => (
              <section
                key={rule.id}
                className="mt-4 rounded-xl border border-slate-200 p-4"
              >
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="font-semibold">
                    {t(rule.scope === 'case' ? 'app.config.caseRule' : 'app.config.rule', { number: identifierNumber(config.identifierRules, index) })}
                  </h3>
                  <Button
                    variant="outline"
                    aria-label={t(rule.scope === 'case' ? 'app.config.removeCaseRule' : 'app.config.removeRule', {
                      number: identifierNumber(config.identifierRules, index)
                    })}
                    onClick={() => {
                      setConfig((c) => ({
                        ...c,
                        identifierRules: c.identifierRules.filter(
                          (r) => r.id !== rule.id
                        )
                      }))
                      setDirty(true)
                      setMessage(null)
                    }}
                  >
                    <Trash2 size={15} />
                    {t('app.config.remove')}
                  </Button>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(e) =>
                      updateRule(rule.id, { enabled: e.target.checked })
                    }
                    className="accent-teal-700"
                  />
                  {t('identifier.enabled')}
                </label>
                <fieldset className="mt-3">
                  <legend className="mb-2 text-sm font-medium">
                    {t('identifier.resources')}
                  </legend>
                  <div className="grid gap-2 lg:grid-cols-2">
                    {identifierChoices.map((choice) => {
                      const resource = choice.selector
                      return (
                      <label
                        key={resource}
                        className="flex items-center gap-2 text-sm"
                      >
                        <input
                          type="checkbox"
                          checked={rule.resources.includes(resource)}
                          onChange={(e) =>
                            updateRule(rule.id, {
                              resources: e.target.checked
                                ? [...rule.resources, resource]
                                : rule.resources.filter((r) => r !== resource)
                            })
                          }
                          className="accent-teal-700"
                        />
                        <span className="flex flex-wrap items-center gap-1">
                          <span>{t(choice.labelKey)}{choice.level && ` · ${t('choice.' + choice.level)}`}</span>
                          <span className="rounded border border-slate-200 bg-slate-100 px-2 py-1 text-xs text-slate-600">
                            {t('app.config.fhirResource', { resource: choice.resourceType })}
                            {choice.classCode && ` · ${t('app.config.encounterClass', { code: choice.classCode })}`}
                            {choice.category && ` · ${t('app.config.category')}: ${choice.category}`}
                          </span>
                        </span>
                      </label>
                    )})}
                  </div>
                </fieldset>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <label className="flex min-w-0 flex-col gap-2 text-sm">
                    <span className="flex min-h-7 items-center">{t('identifier.system')}</span>
                    <input
                      placeholder="https://example.org/fhir/sid/test-id"
                      value={rule.system}
                      onChange={(e) =>
                        updateRule(rule.id, { system: e.target.value })
                      }
                      className="h-10 w-full min-w-0 rounded-lg border border-slate-300 p-2 placeholder:text-slate-400 focus:placeholder:text-transparent"
                    />
                  </label>
                  <IdentifierPatternControl
                    scope={rule.scope}
                    value={rule.pattern}
                    countStart={rule.countStart ?? 1}
                    onCountStartChange={(countStart) => updateRule(rule.id, { countStart })}
                    onChange={(pattern) => updateRule(rule.id, { pattern })}
                    t={t}
                  />
                </div>
                <RulePreview rule={rule} t={t} />
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div className="flex min-w-0 flex-col gap-2 text-sm">
                    <span className="flex min-h-7 items-center gap-1"><label htmlFor={`identifier-use-${rule.id}`}>{t('identifier.use')}</label><Help text={t('identifier.useHelp')} t={t}/></span>
                    <select id={`identifier-use-${rule.id}`} className="h-10 w-full min-w-0 rounded-lg border border-slate-300 p-2" value={rule.use ?? ''} onChange={e => updateRule(rule.id, { use: e.target.value })}>
                      <option value="">{t('identifier.unspecified')}</option>
                      {identifierBindings.use.map(value => <option key={value.code} value={value.code}>{value.code} — {value.display}</option>)}
                    </select>
                  </div>
                  <div className="flex min-w-0 flex-col gap-2 text-sm">
                    <span className="flex min-h-7 items-center gap-1"><label htmlFor={`identifier-type-text-${rule.id}`}>{t('identifier.type_text')}</label><Help text={t('identifier.typeTextHelp')} t={t}/></span>
                    <input id={`identifier-type-text-${rule.id}`} className="h-10 w-full min-w-0 rounded-lg border border-slate-300 p-2" value={rule.typeText ?? ''} onChange={e => updateRule(rule.id, { typeText: e.target.value })}/>
                  </div>
                </div>
                <div className="mt-4 space-y-3">
                  <div className="flex items-center gap-2"><h4 className="text-sm font-medium">{t('identifier.type_codings')}</h4><Help text={t('identifier.typeHelp')} t={t}/></div>
                  {(rule.typeCodings ?? []).map((coding, codingIndex) => {
                    const updateCoding = (value: typeof coding) => updateRule(rule.id, { typeCodings: rule.typeCodings!.map((item, i) => i === codingIndex ? value : item) })
                    return <div key={codingIndex} className="space-y-2 rounded-lg border p-3">
                      <select aria-label={t('identifier.chooseType')} className="w-full rounded-lg border border-slate-300 p-2 text-sm" value={identifierBindings.types.some(item => item.system === coding.system && item.code === coding.code) ? `${coding.system}|${coding.code}` : ''} onChange={e => { const value = identifierBindings.types.find(item => `${item.system}|${item.code}` === e.target.value); if (value) updateCoding({ ...value }) }}>
                        <option value="">{t('identifier.chooseType')}</option>
                        {identifierBindings.types.map(item => <option key={`${item.system}|${item.code}`} value={`${item.system}|${item.code}`}>{item.code} — {t('identifier.typeLabel.' + item.code)}</option>)}
                      </select>
                      <div className="grid gap-3 md:grid-cols-3">{(['system', 'code', 'display'] as const).map(field => <label key={field} className="flex min-w-0 flex-col gap-1 text-sm">{t(`identifier.coding.${field}`)}<input className="h-10 w-full min-w-0 rounded-lg border border-slate-300 p-2" value={coding[field] ?? ''} onChange={e => updateCoding({ ...coding, [field]: e.target.value })}/></label>)}</div>
                      <Button variant="outline" onClick={() => updateRule(rule.id, { typeCodings: rule.typeCodings!.filter((_, i) => i !== codingIndex) })}>{t('app.config.remove')}</Button>
                    </div>
                  })}
                  <Button variant="outline" onClick={() => updateRule(rule.id, { typeCodings: [...(rule.typeCodings ?? []), { system: 'http://terminology.hl7.org/CodeSystem/v2-0203', code: '', display: '' }] })}>{t('identifier.addType')}</Button>
                </div>
              </section>
            ))}
          </>
        )}
        </fieldset>
      </div>
    </section>
  )
}
