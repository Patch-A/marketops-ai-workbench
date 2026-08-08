import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const auditPath = path.join(root, 'validation', 'results', 'm0-05-dependency-audit.json');
const reportPath = path.join(root, 'docs', 'M0_05_DEPENDENCY_DECISION.md');
const requiredInputs = new Set([
  'docs/OPEN_SOURCE_REVIEW.md',
  'docs/M0_DOCUMENT_PARSING_SPIKE.md',
  'docs/M0_SCHEDULING_SPIKE.md',
  'docs/M0_HYBRID_RETRIEVAL_SPIKE.md',
  'docs/M0_05_DEPENDENCY_DECISION.md',
]);
const expectedCandidates = new Map(Object.entries({
  'postgresql-fts': { decision: 'adopted', version: '18.4' },
  pgvector: { decision: 'adopted', version: '0.8.6' },
  procrastinate: { decision: 'adopted', version: '3.9.0' },
  docling: { decision: 'deferred', version: '2.118.1' },
  langgraph: { decision: 'deferred', version: '1.2.10' },
  unstructured: { decision: 'deferred', version: '0.25.2' },
  'sentence-transformers': { decision: 'deferred', version: '5.7.0' },
  flagembedding: { decision: 'deferred', version: '1.4.0' },
  'or-tools': { decision: 'deferred', version: '9.15' },
  'apache-tika': { decision: 'rejected', version: '3.3.2' },
  qdrant: { decision: 'rejected', version: '1.19.0' },
  dify: { decision: 'rejected', version: '1.16.1' },
  n8n: { decision: 'rejected', version: 'n8n@2.33.7' },
  minio: { decision: 'rejected', version: 'RELEASE.2025-10-15T17-29-55Z' },
  rsshub: { decision: 'rejected', version: 'not-pinned' },
}));
const decisions = new Set(['adopted', 'deferred', 'rejected']);
const integrationModes = new Set(['core_dependency', 'optional_adapter', 'external_service', 'reference_only']);
const apacheCoreCompatibleLicenses = new Set([
  'Apache-2.0',
  'MIT',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'ISC',
  'PostgreSQL',
  'Zlib',
]);
const restrictedLicensePatterns = [
  /AGPL/i,
  /SSPL/i,
  /BUSL/i,
  /BSL/i,
  /FAIR[- ]?CODE/i,
  /ELASTIC[- ]?2\.0/i,
  /COMMONS[- ]?CLAUSE/i,
  /LICENSE[- ]?REF/i,
  /CUSTOM/i,
  /NOASSERTION/i,
  /OTHER/i,
  /UNKNOWN/i,
];
const starPattern = /\b(?:github\s*)?stars?\b|\bstar\s*count\b|\u661f\u6807|\bstar\s*\u6570\b|\u5173\u6ce8\u5ea6|\u70ed\u5ea6/i;

const failures = [];

function fail(message) {
  failures.push(message);
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function requireObject(value, label) {
  if (!isObject(value)) {
    fail(`${label} must be an object.`);
    return {};
  }
  return value;
}

function requireString(value, label) {
  if (typeof value !== 'string' || value.trim() === '') {
    fail(`${label} must be a non-empty string.`);
    return '';
  }
  return value.trim();
}

function requireStringArray(value, label) {
  if (!Array.isArray(value) || value.length === 0) {
    fail(`${label} must be a non-empty array.`);
    return [];
  }
  value.forEach((item, index) => requireString(item, `${label}[${index}]`));
  return value;
}

function requireRationaleArray(value, label) {
  if (!Array.isArray(value) || value.length === 0) {
    fail(`${label} must be a non-empty array.`);
    return [];
  }
  const classifications = new Set();
  value.forEach((rawItem, index) => {
    const item = requireObject(rawItem, `${label}[${index}]`);
    const classification = requireString(item.classification, `${label}[${index}].classification`);
    const text = requireString(item.text, `${label}[${index}].text`);
    if (!['fact', 'inference', 'unknown'].includes(classification)) {
      fail(`${label}[${index}].classification must be fact, inference, or unknown.`);
    }
    classifications.add(classification);
    if (starPattern.test(text)) fail(`${label}[${index}].text uses popularity as decision evidence.`);
  });
  for (const classification of ['fact', 'inference', 'unknown']) {
    if (!classifications.has(classification)) fail(`${label} must include ${classification}.`);
  }
  return value;
}

function requireIsoDate(value, label) {
  const text = requireString(value, label);
  if (text && Number.isNaN(Date.parse(text))) fail(`${label} must be an ISO-compatible date.`);
  return text;
}

function requireReleaseDate(value, label, decision) {
  if (value === null && decision !== 'adopted') return null;
  return requireIsoDate(value, label);
}

function requireHttpUrl(value, label) {
  const text = requireString(value, label);
  if (!text) return text;
  try {
    const parsed = new URL(text);
    if (!['http:', 'https:'].includes(parsed.protocol)) fail(`${label} must use HTTP(S).`);
  } catch {
    fail(`${label} must be a valid URL.`);
  }
  return text;
}

function normalizeRelativePath(value, label) {
  const text = requireString(value, label).replaceAll('\\', '/');
  if (!text) return '';
  if (path.isAbsolute(text) || text.split('/').includes('..')) {
    fail(`${label} must be a repository-relative path without '..'.`);
    return '';
  }
  return text;
}

function sha256(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function checkInputs(inputs) {
  if (!Array.isArray(inputs) || inputs.length === 0) {
    fail('inputs must be a non-empty array.');
    return;
  }

  const seen = new Set();
  inputs.forEach((rawInput, index) => {
    const input = requireObject(rawInput, `inputs[${index}]`);
    const relativePath = normalizeRelativePath(input.path, `inputs[${index}].path`);
    const recordedHash = requireString(input.sha256, `inputs[${index}].sha256`).toLowerCase();
    if (!relativePath) return;
    if (seen.has(relativePath)) fail(`inputs contains duplicate path ${relativePath}.`);
    seen.add(relativePath);
    const fullPath = path.join(root, ...relativePath.split('/'));
    if (!fs.existsSync(fullPath) || !fs.statSync(fullPath).isFile()) {
      fail(`input file is missing: ${relativePath}.`);
      return;
    }
    const actualHash = sha256(fullPath);
    if (!/^[a-f0-9]{64}$/.test(recordedHash)) fail(`${relativePath} has an invalid SHA-256 value.`);
    else if (actualHash !== recordedHash) fail(`${relativePath} hash is stale; regenerate the dependency audit.`);
  });

  for (const requiredPath of requiredInputs) {
    if (!seen.has(requiredPath)) fail(`required freshness input is missing: ${requiredPath}.`);
  }
}

function checkCandidate(rawCandidate, index, ids) {
  const label = `candidates[${index}]`;
  const candidate = requireObject(rawCandidate, label);
  const id = requireString(candidate.id, `${label}.id`);
  requireString(candidate.name, `${label}.name`);
  requireString(candidate.category, `${label}.category`);
  const decision = requireString(candidate.decision, `${label}.decision`);
  const version = requireString(candidate.version, `${label}.version`);
  if (id && ids.has(id)) fail(`${label}.id duplicates ${id}.`);
  ids.add(id);
  if (decision && !decisions.has(decision)) fail(`${label}.decision must be adopted, deferred, or rejected.`);
  if (decision === 'adopted' && /^(?:latest|main|master|head|unknown|n\/a)$/i.test(version)) {
    fail(`${label}.version must pin a release or immutable revision for an adopted candidate.`);
  }

  const license = requireObject(candidate.license, `${label}.license`);
  const spdx = requireString(license.spdx, `${label}.license.spdx`);
  const licenseStatus = requireString(license.status, `${label}.license.status`);
  requireHttpUrl(license.sourceUrl, `${label}.license.sourceUrl`);
  requireIsoDate(license.verifiedAt, `${label}.license.verifiedAt`);
  if (decision === 'adopted' && licenseStatus !== 'verified') {
    fail(`${label} is adopted but its license.status is not verified.`);
  }

  const source = requireObject(candidate.source, `${label}.source`);
  requireHttpUrl(source.repositoryUrl, `${label}.source.repositoryUrl`);
  requireHttpUrl(source.releaseUrl, `${label}.source.releaseUrl`);

  const maintenance = requireObject(candidate.maintenance, `${label}.maintenance`);
  requireIsoDate(maintenance.checkedAt, `${label}.maintenance.checkedAt`);
  requireReleaseDate(maintenance.latestReleaseAt, `${label}.maintenance.latestReleaseAt`, decision);
  if (typeof maintenance.repositoryArchived !== 'boolean') {
    fail(`${label}.maintenance.repositoryArchived must be boolean.`);
  }
  requireStringArray(maintenance.signals, `${label}.maintenance.signals`);
  requireString(maintenance.assessment, `${label}.maintenance.assessment`);
  if (decision === 'adopted' && maintenance.repositoryArchived === true) {
    fail(`${label} adopts an archived repository.`);
  }

  const rationale = requireRationaleArray(candidate.rationale, `${label}.rationale`);
  requireStringArray(candidate.alternatives, `${label}.alternatives`);
  const fallback = decision === 'adopted'
    ? requireString(candidate.fallback, `${label}.fallback`)
    : candidate.fallback;
  const privateDeployment = decision === 'adopted'
    ? requireString(candidate.privateDeployment, `${label}.privateDeployment`)
    : candidate.privateDeployment;
  const integration = requireObject(candidate.coreIntegration, `${label}.coreIntegration`);
  const mode = requireString(integration.mode, `${label}.coreIntegration.mode`);
  const boundary = requireString(integration.boundary, `${label}.coreIntegration.boundary`);
  if (mode && !integrationModes.has(mode)) {
    fail(`${label}.coreIntegration.mode must be one of ${[...integrationModes].join(', ')}.`);
  }

  if (candidate.starPolicy !== 'not_a_decision_factor') {
    fail(`${label}.starPolicy must equal "not_a_decision_factor".`);
  }
  if (decision === 'adopted' && rationale.some((item) => starPattern.test(item?.text ?? ''))) {
    fail(`${label}.rationale uses GitHub stars or popularity as an adoption reason.`);
  }

  const restrictedLicense = restrictedLicensePatterns.some((pattern) => pattern.test(spdx));
  if (decision === 'adopted' && mode === 'core_dependency' && !apacheCoreCompatibleLicenses.has(spdx)) {
    fail(`${label} puts non-approved or custom license ${spdx} into the Apache-2.0 core.`);
  }
  if (decision === 'adopted' && restrictedLicense && !['optional_adapter', 'external_service'].includes(mode)) {
    fail(`${label} with restricted license ${spdx} must use an explicit adapter or external-service boundary.`);
  }
  if (decision === 'adopted' && restrictedLicense && boundary.length < 20) {
    fail(`${label}.coreIntegration.boundary is too vague for restricted license isolation.`);
  }
  if (decision === 'adopted' && (!fallback || !privateDeployment)) fail(`${label} must document fallback and private deployment for adoption.`);
}

function checkSummary(summary, candidates) {
  const value = requireObject(summary, 'summary');
  const counts = Object.fromEntries([...decisions].map((decision) => [
    decision,
    candidates.filter((candidate) => candidate?.decision === decision).length,
  ]));
  for (const decision of decisions) {
    if (!Number.isInteger(value[decision]) || value[decision] < 0) {
      fail(`summary.${decision} must be a non-negative integer.`);
    } else if (value[decision] !== counts[decision]) {
      fail(`summary.${decision} is stale: expected ${counts[decision]}, got ${value[decision]}.`);
    }
  }
  if (!Array.isArray(value.blockers)) fail('summary.blockers must be an array.');
  else {
    value.blockers.forEach((item, index) => requireString(item, `summary.blockers[${index}]`));
    if (value.blockers.length > 0) fail('summary.blockers must be empty before M0-05 can pass.');
  }
}

function checkFrozenDecision(candidates) {
  if (candidates.length !== expectedCandidates.size) {
    fail(`candidate set must contain exactly ${expectedCandidates.size} frozen decisions.`);
  }
  const byId = new Map(candidates.map((candidate) => [candidate?.id, candidate]));
  for (const [id, expected] of expectedCandidates) {
    const candidate = byId.get(id);
    if (!candidate) {
      fail(`frozen candidate is missing: ${id}.`);
      continue;
    }
    if (candidate.decision !== expected.decision) {
      fail(`${id}.decision must remain ${expected.decision}.`);
    }
    if (candidate.version !== expected.version) {
      fail(`${id}.version must remain ${expected.version}.`);
    }
  }

  const docling = byId.get('docling');
  if (docling?.coreIntegration?.mode !== 'optional_adapter') {
    fail('docling must remain an optional adapter while deferred.');
  }
  const doclingBoundary = docling?.coreIntegration?.boundary ?? '';
  if (!/isolated/i.test(doclingBoundary) || !/(?:worker|process)/i.test(doclingBoundary)) {
    fail('docling boundary must require an isolated Worker process.');
  }

  if (!fs.existsSync(reportPath)) {
    fail('dependency decision report is missing.');
    return;
  }
  const report = fs.readFileSync(reportPath, 'utf8');
  for (const [id, expected] of expectedCandidates) {
    const row = `| \`${id}\` | ${expected.decision} | \`${expected.version}\` |`;
    if (!report.includes(row)) fail(`dependency report matrix is stale for ${id}.`);
  }
}

function main() {
  if (!fs.existsSync(auditPath)) {
    console.error('Dependency decision audit is missing: validation/results/m0-05-dependency-audit.json');
    process.exit(1);
  }

  let audit;
  try {
    audit = JSON.parse(fs.readFileSync(auditPath, 'utf8'));
  } catch (error) {
    console.error(`Dependency decision audit is not valid JSON: ${error.message}`);
    process.exit(1);
  }

  if (!isObject(audit)) fail('audit root must be an object.');
  if (audit.schemaVersion !== 1) fail('schemaVersion must equal 1.');
  const generatedAt = requireIsoDate(audit.generatedAt, 'generatedAt');
  if (generatedAt && Date.parse(generatedAt) > Date.now() + 5 * 60 * 1000) {
    fail('generatedAt cannot be in the future.');
  }
  checkInputs(audit.inputs);

  if (!Array.isArray(audit.candidates) || audit.candidates.length === 0) {
    fail('candidates must be a non-empty array.');
  } else {
    const ids = new Set();
    audit.candidates.forEach((candidate, index) => checkCandidate(candidate, index, ids));
    checkFrozenDecision(audit.candidates);
    checkSummary(audit.summary, audit.candidates);
  }

  if (failures.length > 0) {
    console.error(failures.join('\n'));
    process.exit(1);
  }

  console.log(`Dependency decision passed: ${audit.candidates.length} candidates, fresh inputs, no unresolved blockers.`);
}

main();
