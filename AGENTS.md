# Repository Notes for Codex

## Project

- The GitHub repository is `medizininformatik-initiative/synthifhir`.
- The main integration branch for current work is `develop`.
- Prefer small, reviewable pull requests.
- Ship a ready-to-run workbook in `input/`. When maintaining the template, update its bundled starter copy there as well.
- Write user documentation in English and describe current behavior directly. Use “KDS variants”, keep examples focused on essential parameters, and link to shared explanations instead of repeating them.
- Assume familiarity with Git, Docker and spreadsheet editors. Focus documentation on project-specific input, commands and results; omit generic setup and first-build explanations.
- Omit repository checkout instructions. Branch-specific links in user documentation must use `main`.

## Synthea mapping: completeness first

- This priority applies to all agents and threads working on Synthea import and mapping in this project: produce the most complete usable test data possible.
- Keep intentional exclusions for US documentation concepts without a useful German representation. Missing translations alone do not establish irrelevance. Use existing German text mappings and report unknown free text instead of inserting untranslated English into clinical observation or CarePlan fields.
- Synthea patients are invented. Preserve the medical meaning and patient story where practical, but completeness takes precedence over medical accuracy. When faithful mapping is impractical, use approximations, representative German products or other documented workarounds.
- Retain available records, fields and mappings even when the result is medically inconsistent or would be considered incorrect in real patient care. Do not suppress data, clear fields or block generation solely because medical equivalence, dosage compatibility or clinical plausibility is uncertain.
- Preserve supplied dosage values, units, frequency and text within the supported input representation. Choose or approximate the mapping around those facts; do not rewrite existing dosages to make a replacement product medically consistent. Missing values remain missing unless an explicitly agreed synthetic enrichment applies.
- Keep source provenance and document approximations or representation limits in the report. Medical review notes are informational and must not become exclusion rules. If a technical representation limit prevents transfer, seek a workaround and make any remaining loss visible.
- Apply this principle consistently across medication, diagnoses, procedures and other Synthea-derived data, including audits and tests. Tests should protect data retention and explicit source values rather than enforce medically perfect synthetic cases.

## User interface copy

- Describe controls and actual behavior in the interface and configuration exports. Keep ticket numbers, implementation progress and planned integration work in tickets or project notes, never in user-facing copy.

## Build and Validation

- Use Maven for local validation.
- Run `mvn test` for normal code and documentation-only changes.
- Run `mvn test package` when dependency, packaging, Docker image, or shaded JAR contents may be affected.
- CI runs CodeQL in the `test` job and Trivy in the `security-scan` job.
- After merging security-related PRs, wait for the push CI run on `develop` before trusting the GitHub code-scanning alert list. Alerts can remain stale until the new SARIF upload finishes.

## Formatting

- Java formatting is defined by the committed Eclipse settings in `.settings/org.eclipse.jdt.core.prefs` and `.settings/org.eclipse.jdt.ui.prefs`.
- Keep formatting-only changes in their own commit or PR when possible.
- Avoid committing local IDE metadata beyond the explicitly tracked `.settings` files.

## GitHub Workflow

- The local environment uses `gh` for GitHub actions such as creating PRs, checking CI, and updating issues.
- For PR checks, `gh pr checks <number> --repo medizininformatik-initiative/synthifhir` gives the concise status.
- For code scanning, query `refs/heads/develop` when verifying the branch state:

```sh
gh api 'repos/medizininformatik-initiative/synthifhir/code-scanning/alerts?ref=refs/heads/develop&state=open&per_page=100'
```

## Security Alert Context

- The previous GitHub Advanced Security cleanup was tracked in issue #29.
- Security/code-scanning fixes were merged through PRs #30, #31, #32, and #33.
- As of the `develop` CI run for commit `80d29ce`, code scanning reported `0` open alerts.
