import de from '../../catalog/options/de.json' with { type: 'json' }
import en from '../../catalog/options/en.json' with { type: 'json' }
import {
  identifierNumber, contract, options, darFields, darResource, defaults, normalize, problems,
  optionEnabled, unmetDependencies, resourceEnabled, dependencyValues,
  type Configuration, type Option, type Value, type Rule
} from './configuration.ts'

// Keep this grammar compatible with java.util.Properties Reader input. Values
// occupy one physical line for a portable, editable configuration file.
function escapeValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t').replace(/\f/g, '\\f')
    .replace(/[\u0085\u2028\u2029]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'))
    .replace(/^\s+/, s => [...s].map(c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')).join(''))
}
function unescapeValue(value: string): string {
  return value.replace(/\\(u[0-9a-fA-F]{4}|.)/g, (_, token: string) => {
    if (token.startsWith('u') && token.length === 5) return String.fromCharCode(parseInt(token.slice(1), 16))
    return ({ n: '\n', r: '\r', t: '\t', f: '\f' } as Record<string, string>)[token] ?? token
  })
}
function encode(value: Value): string {
  return escapeValue(Array.isArray(value) ? value.join(',') : String(value))
}
function decode(value: string, option: Option): Value {
  if (option.type === 'boolean') {
    if (!['true', 'false'].includes(value)) throw new Error('Expected true or false')
    return value === 'true'
  }
  if (option.type === 'integer' || (option.type === 'enum' && typeof option.default === 'number')) {
    if (!/^-?\d+$/.test(value) || !Number.isSafeInteger(Number(value))) throw new Error('Invalid integer')
    return Number(value)
  }
  return option.type === 'set' ? (value === '' ? [] : value.split(',')) : value
}

export function exportPropertiesConfiguration(input: Configuration, language: 'de' | 'en'): string {
  if (problems(input).length) throw new Error('Invalid configuration')
  const config = normalize(input)
  const texts: Record<string, string> = language === 'de' ? de : en
  const t = (key: string) => texts[key]
  const lines: string[] = []
  const comment = (text: string) => {
    for (const paragraph of text.split('\n')) {
      let line = '#'
      for (const word of paragraph.split(/\s+/).filter(Boolean)) {
        if (line.length + word.length + 1 > 88 && line !== '#') { lines.push(line); line = '#' }
        line += ' ' + word
      }
      lines.push(line)
    }
  }
  const assignment = (key: string, value: Value, inactive = false) =>
    lines.push(`${inactive ? '# ' : ''}${key} = ${encode(value)}`)
  comment(t('app.config.fileTitle'))
  comment(t('app.config.fileUsage'))
  assignment(contract.propertiesFormat.versionProperty, contract.propertiesFormat.version)
  for (const option of options) {
    lines.push('')
    comment(t(option.labelKey))
    comment(t(option.helpKey))
    for (const choice of option.choices ?? [])
      comment(`${t('app.config.fileChoices')}: ${choice} — ${t(option.choiceLabelKeys![String(choice)])}`)
    comment(`${t('app.config.fileDefault')}: ${encode(option.default)}`)
    const value = config.values[option.id]
    const choiceDeps = option.choiceDependencies?.[String(value)]
    const inactive = !optionEnabled(option.id, config.values) || unmetDependencies(choiceDeps, config.values).length > 0
    if (inactive) {
      comment(t('app.config.fileInactive'))
      if (option.id.endsWith('.endApplication') && config.values[option.id.replace('.endApplication', '.endPolicy')] === 'preserve') comment(t('app.config.endApplicationInactive'))
      // Include all prerequisites: an immediate prerequisite can itself be inactive.
      const deps = [...(option.enabledWhen ?? []), ...(choiceDeps ?? [])]
      const labels = deps.map(d => {
        const source = options.find(o => o.id === d.option)!
        return `${t(source.labelKey)}: ${dependencyValues(d).map(value => source.choiceLabelKeys?.[String(value)]
          ? t(source.choiceLabelKeys[String(value)]) : String(value)).join(' / ')}`
      }).join('; ')
      comment(t('app.config.requires').replace('{labels}', labels))
    }
    assignment(option.propertyName, value, inactive)
  }
  lines.push('')
  comment(t('app.config.fileDar'))
  for (const field of darFields) {
    lines.push('')
    comment(t(`dar.field.${field.id}.label`))
    comment(t(`dar.help.${field.semanticGroup}`))
    comment(`${t('app.config.fileChoices')}: unchanged, ${field.allowedCodes.join(', ')}`)
    comment(t('app.config.freeDarCode'))
    // Include conditional restrictions exactly as shown by the editor.
    for (const code of Object.keys(field.codeConditions)) {
      comment(`${code}: ${t(code === 'as-text'
        ? 'app.config.narrativeCondition' : 'app.config.notPerformedCondition')}`)
    }
    const inactive = !resourceEnabled(darResource(field), config.values)
    if (inactive) comment(t('app.config.fileResourceInactive'))
    const rule = config.dar[field.id]
    assignment(contract.propertiesFormat.darProperties[field.id as keyof typeof contract.propertiesFormat.darProperties],
      rule?.mode === 'overwrite' ? (rule.code === 'unchanged' || rule.code.startsWith('!dar:') ? '!dar:' + rule.code : rule.code) : 'unchanged', inactive)
    if (rule?.mode === 'overwrite' && rule.onlyWhenMissing) assignment(contract.propertiesFormat.darProperties[field.id as keyof typeof contract.propertiesFormat.darProperties] + '_ONLY_WHEN_MISSING', true, inactive)
  }
  for (const [index, rule] of config.identifierRules.entries()) {
    lines.push('')
    comment(t(rule.scope === 'case' ? 'app.config.caseRule' : 'app.config.rule').replace('{number}', String(identifierNumber(config.identifierRules, index))))
    comment(t(rule.scope === 'case' ? 'app.config.caseRuleHelp' : 'identifier.help'))
    const inactive = !rule.enabled || !rule.resources.some(r => resourceEnabled(r, config.values))
    if (inactive) comment(t('app.config.fileRuleInactive'))
    const prefix = `${contract.propertiesFormat.identifierPrefix}${index + 1}_`
    const fields = { ID: rule.id, ENABLED: rule.enabled, RESOURCES: rule.resources, SYSTEM: rule.system, PATTERN: rule.pattern, ...(rule.scope ? { SCOPE: rule.scope } : {}), ...(rule.countStart !== undefined ? { COUNT_START: rule.countStart } : {}), ...(rule.use ? { USE: rule.use } : {}), ...(rule.typeText ? { TYPE_TEXT: rule.typeText } : {}), ...(rule.typeCodings?.length ? { TYPE_CODINGS: JSON.stringify(rule.typeCodings) } : {}) }
    for (const [name, value] of Object.entries(fields)) {
      comment(t(`identifier.${name.toLowerCase()}`))
      // An explicit false remains executable: it intentionally disables this rule.
      assignment(prefix + name, value, name === 'ENABLED' && !rule.enabled ? false : inactive)
    }
  }
  return lines.join('\n') + '\n'
}

export function parsePropertiesConfiguration(text: string): Configuration {
  const config = defaults()
  const byName = new Map(options.map(o => [o.propertyName, o]))
  const darNames = new Map(Object.entries(contract.propertiesFormat.darProperties).map(([id, name]) => [name, id]))
  const rules = new Map<number, Record<string, string>>()
  const conditions = new Map<string, boolean>()
  const seen = new Set<string>()
  let version = false
  for (const raw of text.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const trimmed = raw.trimStart()
    if (!trimmed || trimmed.startsWith('!')) continue
    const commented = trimmed.startsWith('#')
    const line = commented ? trimmed.slice(1).trimStart() : trimmed
    const match = /^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$/.exec(line)
    if (!match) {
      if (commented) continue
      throw new Error('Expected NAME = VALUE')
    }
    const [, key, encoded] = match
    if (['PID_LAST_NUMBER_INCREASE_INITIAL_OFFSET', 'PID_LAST_NUMBER_INCREASE_LOOP_OFFSET'].includes(key)) {
      if (seen.has(key) || encoded.trim() !== '0') throw new Error('Use patient ID prefixes or suffixes instead of numeric offsets')
      seen.add(key)
      continue
    }
    if (key === 'ADD_MISSING_DIAGNOSES_FROM_SUPER_ENCOUNTER') {
      if (seen.has(key) || !['true', 'false'].includes(encoded.trim())) throw new Error('Invalid retired setting')
      seen.add(key)
      continue
    }
    const option = byName.get(key)
    const conditionField = key.endsWith('_ONLY_WHEN_MISSING') ? darNames.get(key.slice(0, -18)) : undefined
    const field = darNames.get(key)
    const ruleMatch = /^IDENTIFIER_RULE_([1-9]\d*)_(ID|ENABLED|RESOURCES|SYSTEM|PATTERN|SCOPE|COUNT_START|USE|TYPE_TEXT|TYPE_CODINGS)$/.exec(key)
    if (!option && !field && !conditionField && !ruleMatch && key !== contract.propertiesFormat.versionProperty)
      throw new Error(`Unknown property: ${key}`)
    if (seen.has(key)) throw new Error(`Duplicate property: ${key}`)
    seen.add(key)
    // Reject continuations and malformed escapes rather than silently changing values.
    if (/\\(?:u(?![0-9a-fA-F]{4})|$)/.test(encoded.replace(/\\\\/g, '')))
      throw new Error(`Invalid escape: ${key}`)
    const value = unescapeValue(encoded)
    if (key === contract.propertiesFormat.versionProperty) {
      if (commented || value !== String(contract.propertiesFormat.version)) throw new Error('Unsupported configuration version')
      version = true
    } else if (conditionField) {
      if (!['true', 'false'].includes(value)) throw new Error('Invalid DAR condition')
      conditions.set(conditionField, value === 'true')
    } else if (option) config.values[option.id] = decode(value, option)
    else if (field) {
      if (value !== 'unchanged') config.dar[field] = { mode: 'overwrite', code: value.startsWith('!dar:') ? value.slice(5) : value }
    } else if (ruleMatch) {
      const index = Number(ruleMatch[1])
      if (!Number.isSafeInteger(index)) throw new Error('Invalid rule index')
      const rule = rules.get(index) ?? {}
      rule[ruleMatch[2]] = value
      rules.set(index, rule)
    }
  }
  for (const [field, condition] of conditions) {
    const rule = config.dar[field]
    if (rule?.mode === 'overwrite') rule.onlyWhenMissing = condition
  }
  if (!version) throw new Error('Missing configuration version')
  const entries = [...rules.entries()].sort(([a], [b]) => a - b)
  config.identifierRules = entries.map(([index, rule], offset): Rule => {
    if (index !== offset + 1 || !['ID', 'ENABLED', 'RESOURCES', 'SYSTEM', 'PATTERN'].every(key => key in rule) || !['true', 'false'].includes(rule.ENABLED))
      throw new Error('Incomplete identifier rule')
    return { id: rule.ID, enabled: rule.ENABLED === 'true', resources: rule.RESOURCES.split(','), system: rule.SYSTEM, pattern: rule.PATTERN, ...(rule.SCOPE ? { scope: rule.SCOPE as Rule['scope'] } : {}), ...(rule.COUNT_START !== undefined ? { countStart: decode(rule.COUNT_START, { type: 'integer' } as Option) as number } : {}), ...(rule.USE ? { use: rule.USE } : {}), ...(rule.TYPE_TEXT ? { typeText: rule.TYPE_TEXT } : {}), ...(rule.TYPE_CODINGS ? { typeCodings: JSON.parse(rule.TYPE_CODINGS) } : {}) }
  })
  if (problems(config).length) throw new Error('Invalid configuration')
  return config
}

/** Read the Properties snapshots written by earlier default-only runs. */
export function importRunProperties(text: string): Configuration {
  if (/^CONFIGURATION_VERSION\s*=/m.test(text)) return parsePropertiesConfiguration(text)
  const converted = text.split(/\r?\n/).map(line => {
    const match = /^(SET_REFERENCE_FROM_CONDITION_TO_ENCOUNTER|SET_REFERENCE_FROM_PROCEDURE_CONDITION_TO_ENCOUNTER)\s*=\s*(true|false)\s*$/.exec(line)
    if (match) return `${match[1].includes('PROCEDURE') ? 'REFERENCE_PROCEDURE_ENCOUNTER' : 'REFERENCE_CONDITION_ENCOUNTER'}=${match[2] === 'true' ? 'department' : 'none'}`
    if (/^SYNTHEA_VERSION_OUTPUT\s*=/.test(line)) {
      const value = line.split('=', 2)[1].trim()
      if (value === 'Jahr' || value === '') return `TERMINOLOGY_VERSION_OUTPUT=${value ? 'catalogue-year' : 'omit'}`
    }
    return line
  })
  return parsePropertiesConfiguration('CONFIGURATION_VERSION=1\n' + converted.join('\n'))
}
