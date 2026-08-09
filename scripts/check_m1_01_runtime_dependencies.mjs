import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const auditPath = path.join(root, 'validation', 'results', 'm1-01-runtime-dependency-admission.json');
const reportPath = path.join(root, 'docs', 'M1_01_RUNTIME_DEPENDENCY_DECISION.md');
const baseline = '9d5111990ff99eb8f7a97cb98398ef2a18a73781';
const frozenAt = '2026-08-09T00:00:00+08:00';
const frozenDate = '2026-08-09';
const frozenReportClaim = 'Claim boundary (machine-checked): this decision does not install dependencies or prove runtime or production readiness.';
// Claims are checked at sentence/clause level so explicit negation remains a
// valid boundary statement while a later positive clause is still detected.
const englishPositivePredicate = /\b(?:(?:is|are|has been|have been|will be|can be)\s+)?(?:already\s+)?(?:installed|running|validated|production[- ]validated|production[- ]ready|ready\s+for\s+production(?:\s+use)?)\b/giu;
const chinesePositivePredicate = /(?:已完成生产验证|已安装|已经安装|已运行|已经运行|运行中|已验证|已经验证|生产就绪|可生产使用|可用于生产)/gu;
const subjectConnector = /\b(?:and|or)\b|[,，]|并|且/giu;
const claimSentenceSeparator = /(\b(?:but|however)\b|但|但是|却|\.(?=\s|$)|[!?。！？；;])/giu;
const explicitNegation = /(?:\b(?:not yet|not|never|unvalidated)\b|未|尚未|没有|并非|不是|并不|不可|不(?!仅))/iu;
const outerClaimNegation = /\b(?:do|does|did)\s+not\s+(?:claim|assert|state|say)\b|不声称|不宣称|并非宣称/iu;

function escapeRegExp(value) { return value.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&'); }

function controlledSubjectRegex() {
  const names = Object.values(frozenCandidates).map((candidate) => candidate.name)
    .concat(['selected runtime stack', 'runtime stack', 'selected combination', 'runtime combination', 'dependencies', 'packages', 'candidates']);
  const english = names.filter((name) => /^[\x00-\x7F]+$/.test(name)).sort((a, b) => b.length - a.length).map(escapeRegExp).join('|');
  const chinese = ['\\u4f9d\\u8d56', '\\u8f6f\\u4ef6\\u5305', '\\u5019\\u9009\\u9879', '\\u8fd0\\u884c\\u65f6\\u6808', '\\u8fd0\\u884c\\u65f6\\u7ec4\\u5408', '\\u9009\\u5b9a\\u7ec4\\u5408'].join('|');
  return new RegExp(`(?:\\b(?:${english})\\b|${chinese})`, 'giu');
}

function claimClauses(line) {
  const clauses = [];
  let start = 0;
  let match;
  while ((match = claimSentenceSeparator.exec(line)) !== null) {
    if (line.slice(start, match.index).trim()) clauses.push({ text: line.slice(start, match.index), reset: /[.!?。！？]/u.test(match[0]) });
    start = match.index + match[0].length;
  }
  if (line.slice(start).trim()) clauses.push({ text: line.slice(start), reset: false });
  claimSentenceSeparator.lastIndex = 0;
  return clauses;
}

function hasPositiveClaim(line) {
  const subjectRegex = controlledSubjectRegex();
  let inherited = [];
  for (const clause of claimClauses(line)) {
    const text = clause.text.trim();
    const subjects = [...text.matchAll(subjectRegex)].map((m) => ({ index: m.index, end: m.index + m[0].length, value: m[0] }));
    const effective = subjects.length > 0 ? subjects : inherited.map((subject) => ({ index: -1, end: 0, value: subject.value }));
    const outerNegated = outerClaimNegation.test(text.slice(0, subjects[0]?.index ?? text.length));
    let violation = false;
    for (const subject of effective) {
      const tail = text.slice(subject.end);
      const predicates = [...tail.matchAll(englishPositivePredicate), ...tail.matchAll(chinesePositivePredicate)]
        .sort((a, b) => a.index - b.index);
      let previousNegated = false;
      for (const predicate of predicates) {
        const before = tail.slice(0, predicate.index);
        const connectorMatches = [...before.matchAll(subjectConnector)];
        const connector = connectorMatches.at(-1);
        const scope = before.slice(connector ? connector.index + connector[0].length : 0);
        const localNegated = explicitNegation.test(scope);
        const inheritedNegation = Boolean(connector) && scope.trim() === '' && previousNegated;
        const predicateNegated = outerNegated || localNegated || inheritedNegation;
        if (!predicateNegated) { violation = true; break; }
        previousNegated = predicateNegated;
      }
      if (violation) break;
    }
    if (violation) return true;
    inherited = subjects;
    if (clause.reset) inherited = [];
  }
  return false;
}

const frozenCandidates = {
  fastapi: {
    name: 'FastAPI', capability: 'http_framework', decision: 'adopted', version: '0.141.1',
    package: {
      type: 'pypi', name: 'fastapi', requiresPython: '>=3.10', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/fastapi/0.141.1/json',
      releaseUrl: 'https://github.com/fastapi/fastapi/releases/tag/0.141.1',
      repositoryUrl: 'https://github.com/fastapi/fastapi',
    },
    license: { spdx: 'MIT', sourceUrl: 'https://github.com/fastapi/fastapi/blob/0.141.1/LICENSE' },
  },
  litestar: {
    name: 'Litestar', capability: 'http_framework', decision: 'deferred', version: '2.24.0',
    package: {
      type: 'pypi', name: 'litestar', requiresPython: '>=3.8,<4.0', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/litestar/2.24.0/json',
      releaseUrl: 'https://github.com/litestar-org/litestar/releases/tag/v2.24.0',
      repositoryUrl: 'https://github.com/litestar-org/litestar',
    },
    license: { spdx: 'MIT', sourceUrl: 'https://github.com/litestar-org/litestar/blob/v2.24.0/LICENSE' },
  },
  uvicorn: {
    name: 'Uvicorn', capability: 'asgi_server', decision: 'adopted', version: '0.52.1',
    package: {
      type: 'pypi', name: 'uvicorn', requiresPython: '>=3.10', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/uvicorn/0.52.1/json',
      releaseUrl: 'https://github.com/Kludex/uvicorn/releases/tag/0.52.1',
      repositoryUrl: 'https://github.com/Kludex/uvicorn',
    },
    license: { spdx: 'BSD-3-Clause', sourceUrl: 'https://github.com/Kludex/uvicorn/blob/0.52.1/LICENSE.md' },
  },
  'python-multipart': {
    name: 'python-multipart', capability: 'multipart_parser', decision: 'adopted', version: '0.0.32',
    package: {
      type: 'pypi', name: 'python-multipart', requiresPython: '>=3.10', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/python-multipart/0.0.32/json',
      releaseUrl: 'https://github.com/Kludex/python-multipart/releases/tag/0.0.32',
      repositoryUrl: 'https://github.com/Kludex/python-multipart',
    },
    license: { spdx: 'Apache-2.0', sourceUrl: 'https://github.com/Kludex/python-multipart/blob/0.0.32/LICENSE.txt' },
  },
  psycopg: {
    name: 'Psycopg 3 family', capability: 'postgres_driver', decision: 'deferred', version: '3.3.4',
    package: {
      type: 'pypi', name: 'psycopg', requiresPython: '>=3.10', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/psycopg/3.3.4/json',
      releaseUrl: 'https://github.com/psycopg/psycopg/tree/3.3.4',
      repositoryUrl: 'https://github.com/psycopg/psycopg',
    },
    license: { spdx: 'LGPL-3.0-only', sourceUrl: 'https://github.com/psycopg/psycopg/blob/3.3.4/LICENSE.txt' },
  },
  asyncpg: {
    name: 'asyncpg', capability: 'postgres_driver', decision: 'adopted', version: '0.31.0',
    package: {
      type: 'pypi', name: 'asyncpg', requiresPython: '>=3.9.0', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/asyncpg/0.31.0/json',
      releaseUrl: 'https://github.com/MagicStack/asyncpg/releases/tag/v0.31.0',
      repositoryUrl: 'https://github.com/MagicStack/asyncpg',
    },
    license: { spdx: 'Apache-2.0', sourceUrl: 'https://github.com/MagicStack/asyncpg/blob/v0.31.0/LICENSE' },
  },
  alembic: {
    name: 'Alembic', capability: 'migration_runner', decision: 'deferred', version: '1.19.1',
    package: {
      type: 'pypi', name: 'alembic', requiresPython: '>=3.10', python312Support: 'declared',
      metadataUrl: 'https://pypi.org/pypi/alembic/1.19.1/json',
      releaseUrl: 'https://github.com/sqlalchemy/alembic/releases/tag/rel_1_19_1',
      repositoryUrl: 'https://github.com/sqlalchemy/alembic',
    },
    license: { spdx: 'MIT', sourceUrl: 'https://github.com/sqlalchemy/alembic/blob/rel_1_19_1/LICENSE' },
  },
  'yoyo-migrations': {
    name: 'yoyo-migrations', capability: 'migration_runner', decision: 'rejected', version: '9.0.0',
    package: {
      type: 'pypi', name: 'yoyo-migrations', requiresPython: 'not declared in 9.0.0 wheel metadata', python312Support: 'unknown',
      metadataUrl: 'https://pypi.org/pypi/yoyo-migrations/9.0.0/json',
      releaseUrl: 'https://hg.sr.ht/~olly/yoyo/rev/v9.0.0-release',
      repositoryUrl: 'https://hg.sr.ht/~olly/yoyo',
    },
    license: {
      spdx: 'Apache-2.0',
      sourceUrl: 'https://files.pythonhosted.org/packages/8c/5d/9ef7f808ea955eca9f08043c65bdc81a4694e784c978b24ad72022974a97/yoyo_migrations-9.0.0-py3-none-any.whl',
    },
  },
  'internal-sql-runner': {
    name: 'Project-owned immutable SQL runner contract', capability: 'migration_runner', decision: 'adopted', version: 'contract-v1',
    package: {
      type: 'internal', name: 'internal-sql-runner',
      requiresPython: 'not applicable until implementation transport is selected', python312Support: 'not_applicable',
      metadataUrl: `https://github.com/Patch-A/marketops-ai-workbench/blob/${baseline}/apps/api/migrations/0001_project_import.sql`,
      releaseUrl: null,
      repositoryUrl: `https://github.com/Patch-A/marketops-ai-workbench/tree/${baseline}/apps/api/migrations`,
    },
    license: { spdx: 'Apache-2.0', sourceUrl: `https://github.com/Patch-A/marketops-ai-workbench/blob/${baseline}/LICENSE` },
  },
};

const frozenSelections = {
  http_framework: 'fastapi',
  asgi_server: 'uvicorn',
  multipart_parser: 'python-multipart',
  postgres_driver: 'asyncpg',
  migration_runner: 'internal-sql-runner',
};

const frozenDirect = {
  fastapi: [['starlette', '>=0.46.0'], ['pydantic', '>=2.9.0'], ['typing-extensions', '>=4.8.0'], ['typing-inspection', '>=0.4.2'], ['annotated-doc', '>=0.0.2']],
  litestar: [['anyio', '>3'], ['click', 'unbounded'], ['httpx', '>0.22'], ['litestar-htmx', '>=0.4.0'], ['msgspec', '>=0.18.2'], ['multidict', '>=6.0.2'], ['multipart', '>=1.2.0'], ['polyfactory', '>=2.6.3'], ['pyyaml', 'unbounded'], ['rich-click', 'unbounded'], ['rich', '>=13.0.0'], ['sniffio', '>=1.3.1'], ['typing-extensions', 'unbounded']],
  uvicorn: [['click', '>=7.0'], ['h11', '>=0.8'], ['typing-extensions', '>=4.0; python_version<3.11']],
  'python-multipart': [],
  psycopg: [['typing-extensions', '>=4.6; python_version<3.13'], ['tzdata', 'unbounded; sys_platform==win32']],
  asyncpg: [['async-timeout', '>=4.0.3; python_version<3.11']],
  alembic: [['SQLAlchemy', '>=1.4.23'], ['Mako', 'unbounded'], ['typing-extensions', '>=4.12']],
  'yoyo-migrations': [['sqlparse', 'unbounded'], ['tabulate', 'unbounded'], ['importlib-metadata', '>=3.6.0']],
  'internal-sql-runner': [],
};

const frozenBoundaries = {
  fastapi: 'FastAPI may parse authenticated HTTP and multipart input into ImportRequest, but it cannot accept tenant scope or contain project import business rules.',
  litestar: 'If selected later, Litestar must implement the same OpenAPI-to-service adapter and cannot change authorization or domain contracts.',
  uvicorn: 'Uvicorn owns ASGI process lifecycle and transport settings only; authentication, request limits, and import behavior remain explicit application concerns.',
  'python-multipart': 'The parser may produce bounded file streams and scalar fields only; it cannot decide authorization, approval, retention, or storage keys.',
  psycopg: 'A future Psycopg adapter would own connection pooling, transaction-local RLS scope, SQL parameterization, row mapping, and driver error translation only.',
  asyncpg: 'The implementation must become genuinely async end to end; an AsyncImportRepository owns pool acquisition, transaction-local RLS scope, parameterized SQL, row mapping, and error translation.',
  alembic: 'If adopted later, Alembic must run reviewed migrations under an application-owned advisory lock and cannot own authorization or domain state.',
  'yoyo-migrations': 'Any reconsideration must replace the lock-table behavior with an explicit PostgreSQL advisory-lock deployment contract and keep immutable checksums.',
  'internal-sql-runner': 'Contract-v1 requires immutable versioned files with SHA-256, advisory locking before version reads, migration and version recording in one transaction, forward-only recovery, and no ad hoc SQL splitting.',
};

const frozenFallbacks = {
  fastapi: 'Keep the OpenAPI document and ProjectImportService stable while replacing only the HTTP adapter.',
  litestar: 'Retain FastAPI while revisiting Litestar only after measured adapter limitations appear.',
  uvicorn: 'Replace the ASGI server without changing FastAPI routes, OpenAPI DTOs, or ProjectImportService.',
  'python-multipart': 'Replace the multipart parser behind the HTTP adapter while retaining FilePayload and size validation.',
  psycopg: 'Keep the driver-neutral repository contract and use the admitted asyncpg path unless license policy is explicitly changed.',
  asyncpg: 'Preserve domain request and result types while replacing the async repository adapter after equivalent isolation and transaction tests.',
  alembic: 'Retain immutable SQL files and migrate their checksums into Alembic only after a reviewed conversion plan.',
  'yoyo-migrations': 'Use the internal forward-only contract and revisit only after a newer fixed release closes the evidence gaps.',
  'internal-sql-runner': 'Use a pinned PostgreSQL 18.4 psql transport or later migrate checksums into Alembic after equivalent recovery tests.',
};

const frozenOptional = {
  fastapi: [['fastapi[standard]', 'excluded', 'The standard extra would add CLI, templating, HTTP client, server, and validation packages outside the P0 boundary.']],
  litestar: [['litestar[standard]', 'excluded', 'Would add templating, formatting, and Uvicorn standard extras beyond P0.']],
  uvicorn: [['uvicorn[standard]', 'excluded', 'The standard extra adds httptools, dotenv, YAML, uvloop, watchfiles, and websockets without P0 evidence.']],
  'python-multipart': [],
  psycopg: [
    ['psycopg-c', '==3.3.4', 'Local C build links system libpq and libssl; upstream prefers this mode for production.'],
    ['psycopg-binary', '==3.3.4', 'Prebuilt wheels bundle client libraries whose exact native versions vary by target wheel.'],
    ['psycopg-pool', '==3.3.1', 'Separate sync and async pool package; the core pool extra is not version-pinned.'],
  ],
  asyncpg: [['gssapi-or-sspilib', 'gssauth extra excluded', 'GSS authentication is outside the first private deployment boundary.']],
  alembic: [],
  'yoyo-migrations': [['psycopg2', 'postgres extra, unbounded', 'Default PostgreSQL extra adds a driver whose upstream license label is LGPL with exceptions.']],
  'internal-sql-runner': [],
};

const frozenDistributionConditions = {
  fastapi: ['Retain the upstream copyright and MIT permission notice in distributed copies.'],
  litestar: ['Retain the upstream copyright and MIT permission notice.'],
  uvicorn: ['Retain copyright, conditions, and disclaimer in source and binary distributions.'],
  'python-multipart': ['Retain the Apache-2.0 license and applicable notices when distributing the package.'],
  psycopg: ['The current adopted-license allowlist does not approve LGPL-3.0-only.', 'Any future adoption requires explicit legal and distribution review, license preservation, and artifact-level native library review.'],
  asyncpg: ['Retain the Apache-2.0 license and any artifact notices in distributed copies.'],
  alembic: ['Retain the upstream copyright and MIT permission notice.'],
  'yoyo-migrations': ['The sole PyPI wheel contains the Apache-2.0 LICENSE.txt file.'],
  'internal-sql-runner': ['Keep project-owned runner code under the repository Apache-2.0 license.'],
};

const frozenAlternatives = {
  fastapi: ['litestar 2.24.0', 'a standard-library test adapter without a production HTTP server'],
  litestar: ['fastapi 0.141.1'],
  uvicorn: ['another ASGI server behind the unchanged ASGI application boundary'],
  'python-multipart': ['a separately reviewed streaming multipart adapter'],
  psycopg: ['asyncpg 0.31.0', 'a future driver admitted under an approved license'],
  asyncpg: ['psycopg 3.3.4 after an explicit LGPL policy decision'],
  alembic: ['internal-sql-runner contract-v1', 'yoyo-migrations 9.0.0'],
  'yoyo-migrations': ['internal-sql-runner contract-v1', 'alembic 1.19.1'],
  'internal-sql-runner': ['alembic 1.19.1', 'yoyo-migrations 9.0.0', 'a pinned PostgreSQL 18.4 psql execution image'],
};

const frozenAdoptedSnapshots = {
  fastapi: [
    ['starlette', '1.6.0', 'BSD-3-Clause'],
    ['pydantic', '2.13.4', 'MIT'],
    ['pydantic-core', '2.46.4', 'MIT'],
    ['typing-extensions', '4.16.0', 'PSF-2.0'],
    ['typing-inspection', '0.4.2', 'MIT'],
    ['annotated-doc', '0.0.4', 'MIT'],
    ['annotated-types', '0.7.0', 'MIT'],
    ['anyio', '4.12.1', 'MIT'],
    ['idna', '3.18', 'BSD-3-Clause'],
    ['sniffio', '1.3.1', 'MIT OR Apache-2.0'],
  ],
  uvicorn: [
    ['click', '8.2.1', 'BSD-3-Clause'],
    ['h11', '0.16.0', 'MIT'],
    ['colorama', '0.4.6', 'BSD-3-Clause'],
  ],
  'python-multipart': [],
  asyncpg: [],
  'internal-sql-runner': [],
};

const frozenRequiredSnapshotSubsets = {
  psycopg: [['tzdata', '2025.2', 'Apache-2.0']],
  'yoyo-migrations': [['zipp', '3.23.0', 'MIT']],
};

const allowedAdoptedLicenses = new Set(['Apache-2.0', 'BSD-3-Clause', 'MIT', 'MIT OR Apache-2.0', 'PSF-2.0']);
const rationaleClasses = new Set(['fact', 'inference', 'unknown']);
const exactVersionPattern = /^\d+\.\d+(?:\.\d+)?(?:[a-z]+\d+)?$/i;
const strictTimestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?(?:Z|[+-]\d{2}:\d{2})$/;
const strictDatePattern = /^\d{4}-\d{2}-\d{2}$/;
const popularityPattern = /\bstars?\b|star\s*count|popular(?:ity)?|widely\s+used|downloads?|download\s*count|adoption(?:\s+rate)?|\u661f\u6807|\u70ed\u5ea6|\u706b\u7206|\u4e0b\u8f7d(?:\u91cf|\u6b21\u6570)?|\u5e7f\u6cdb\u4f7f\u7528|\u91c7\u7528(?:\u7387|\u91cf)?/i;
const failures = [];

function fail(message) { failures.push(message); }
function isObject(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function requireObject(value, label) {
  if (!isObject(value)) { fail(`${label} must be an object.`); return null; }
  return value;
}
function requireString(value, label) {
  if (typeof value !== 'string' || value.trim() === '') { fail(`${label} must be a non-empty string.`); return ''; }
  return value.trim();
}
function requireArray(value, label, { allowEmpty = false } = {}) {
  if (!Array.isArray(value) || (!allowEmpty && value.length === 0)) { fail(`${label} must be ${allowEmpty ? 'an' : 'a non-empty'} array.`); return []; }
  return value;
}
function requireExact(actual, expected, label) {
  if (actual !== expected) fail(`${label} must equal ${JSON.stringify(expected)}.`);
}
function isValidCalendarDate(text) {
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const utc = new Date(Date.UTC(year, month - 1, day));
  return month >= 1 && month <= 12 && day >= 1 && utc.getUTCFullYear() === year && utc.getUTCMonth() === month - 1 && utc.getUTCDate() === day;
}
function requireDate(value, label) {
  const text = requireString(value, label);
  if (!strictDatePattern.test(text) || !isValidCalendarDate(text)) fail(`${label} must be an ISO 8601 calendar date.`);
  return text;
}
function requireFrozenDate(value, label) {
  const text = requireDate(value, label);
  requireExact(text, frozenDate, label);
  return text;
}
function requireTimestamp(value, label, { nullable = false } = {}) {
  if (nullable && value === null) return null;
  const text = requireString(value, label);
  const milliseconds = Date.parse(text);
  const calendarDate = text.slice(0, 10);
  if (!strictTimestampPattern.test(text) || !isValidCalendarDate(calendarDate) || Number.isNaN(milliseconds)) {
    fail(`${label} must be a strict ISO 8601 timestamp with timezone.`);
  } else if (milliseconds > Date.now() + 5 * 60 * 1000) {
    fail(`${label} cannot be more than five minutes in the future.`);
  }
  return text;
}
function requireFrozenUrl(value, expected, label, { nullable = false } = {}) {
  if (nullable && value === null && expected === null) return;
  const text = requireString(value, label);
  try {
    const parsed = new URL(text);
    if (parsed.protocol !== 'https:') fail(`${label} must use HTTPS.`);
    if (parsed.username || parsed.password) fail(`${label} must not contain URL userinfo.`);
  } catch {
    fail(`${label} must be a valid URL.`);
  }
  requireExact(text, expected, label);
}
function normalizedName(value) { return value.toLowerCase().replace(/[-_.]+/g, '-'); }

function checkNamedEntries(value, label, fields) {
  if (!Array.isArray(value)) { fail(`${label} must be an array.`); return []; }
  const names = new Set();
  value.forEach((rawEntry, index) => {
    const entry = requireObject(rawEntry, `${label}[${index}]`);
    if (!entry) return;
    const name = requireString(entry.name, `${label}[${index}].name`);
    const key = normalizedName(name);
    if (names.has(key)) fail(`${label} duplicates dependency name ${name}.`);
    names.add(key);
    fields.forEach((field) => requireString(entry[field], `${label}[${index}].${field}`));
  });
  return value;
}

function checkDirectEntries(value, expected, label) {
  const entries = checkNamedEntries(value, label, ['specifier', 'reason']);
  if (entries.length !== expected.length) fail(`${label} must contain exactly ${expected.length} frozen direct dependencies.`);
  const byName = new Map(entries.filter(isObject).map((entry) => [normalizedName(String(entry.name)), entry]));
  expected.forEach(([name, specifier]) => {
    const entry = byName.get(normalizedName(name));
    if (!entry) fail(`${label} is missing frozen direct dependency ${name}.`);
    else {
      requireExact(entry.name, name, `${label}.${name}.name`);
      requireExact(entry.specifier, specifier, `${label}.${name}.specifier`);
    }
  });
}

function checkFrozenOptionalEntries(value, expected, label) {
  const entries = checkNamedEntries(value, label, ['specifier', 'reason']);
  if (entries.length !== expected.length) fail(`${label} must contain exactly ${expected.length} frozen optional or excluded extras.`);
  const byName = new Map(entries.filter(isObject).map((entry) => [String(entry.name), entry]));
  expected.forEach(([name, specifier, reason]) => {
    const entry = byName.get(name);
    if (!entry) { fail(`${label} is missing frozen optional or excluded extra ${name}.`); return; }
    requireExact(entry.name, name, `${label}.${name}.name`);
    requireExact(entry.specifier, specifier, `${label}.${name}.specifier`);
    requireExact(entry.reason, reason, `${label}.${name}.reason`);
  });
}

function checkFrozenDistributionConditions(value, expected, label) {
  const conditions = requireArray(value, label);
  if (conditions.length !== expected.length) fail(`${label} must contain exactly ${expected.length} frozen distribution conditions.`);
  conditions.forEach((condition, index) => requireString(condition, `${label}[${index}]`));
  expected.forEach((condition, index) => requireExact(conditions[index], condition, `${label}[${index}]`));
}

function checkSnapshotEntries(value, label, expected = null) {
  const entries = checkNamedEntries(value, label, ['version', 'license', 'sourceUrl', 'reason']);
  entries.filter(isObject).forEach((entry, index) => {
    if (!exactVersionPattern.test(entry.version)) fail(`${label}[${index}].version must be an exact release.`);
    const metadataUrl = `https://pypi.org/pypi/${entry.name}/${entry.version}/json`;
    requireFrozenUrl(entry.sourceUrl, metadataUrl, `${label}[${index}].sourceUrl`);
    if (/AGPL/i.test(entry.license)) fail(`${label}[${index}] uses a forbidden AGPL license.`);
  });
  if (expected) {
    if (entries.length !== expected.length) fail(`${label} must contain the complete ${expected.length}-package frozen adopted snapshot.`);
    const byName = new Map(entries.filter(isObject).map((entry) => [normalizedName(String(entry.name)), entry]));
    expected.forEach(([name, version, license]) => {
      const entry = byName.get(normalizedName(name));
      if (!entry) { fail(`${label} is missing frozen adopted snapshot package ${name}.`); return; }
      requireExact(entry.name, name, `${label}.${name}.name`);
      requireExact(entry.version, version, `${label}.${name}.version`);
      requireExact(entry.license, license, `${label}.${name}.license`);
      requireExact(entry.sourceUrl, `https://pypi.org/pypi/${name}/${version}/json`, `${label}.${name}.sourceUrl`);
      if (!allowedAdoptedLicenses.has(entry.license)) fail(`${label}.${name} has an unapproved adopted license ${entry.license}.`);
    });
  }
}

function checkRequiredSnapshotSubset(value, expected, label) {
  if (!expected) return;
  // checkSnapshotEntries has already recorded the shape error; do not turn it into a checker crash.
  if (!Array.isArray(value)) return;
  const byName = new Map(value.filter(isObject).map((entry) => [normalizedName(String(entry.name)), entry]));
  expected.forEach(([name, version, license]) => {
    const entry = byName.get(normalizedName(name));
    if (!entry) { fail(`${label} is missing required reviewed snapshot package ${name}.`); return; }
    requireExact(entry.name, name, `${label}.${name}.name`);
    requireExact(entry.version, version, `${label}.${name}.version`);
    requireExact(entry.license, license, `${label}.${name}.license`);
    requireExact(entry.sourceUrl, `https://pypi.org/pypi/${name}/${version}/json`, `${label}.${name}.sourceUrl`);
  });
}

function checkFrozenAlternatives(value, expected, label) {
  const alternatives = requireArray(value, label);
  if (alternatives.length !== expected.length) fail(`${label} must contain exactly ${expected.length} frozen alternatives.`);
  alternatives.forEach((alternative, index) => requireString(alternative, `${label}[${index}]`));
  expected.forEach((alternative, index) => requireExact(alternatives[index], alternative, `${label}[${index}]`));
}

function checkRationale(value, label) {
  const seen = new Set();
  requireArray(value, label).forEach((rawItem, index) => {
    const item = requireObject(rawItem, `${label}[${index}]`);
    if (!item) return;
    const classification = requireString(item.classification, `${label}[${index}].classification`);
    const text = requireString(item.text, `${label}[${index}].text`);
    if (!rationaleClasses.has(classification)) fail(`${label}[${index}].classification is invalid.`);
    if (popularityPattern.test(text)) fail(`${label}[${index}] uses popularity as decision evidence.`);
    seen.add(classification);
  });
  rationaleClasses.forEach((classification) => { if (!seen.has(classification)) fail(`${label} must include ${classification}.`); });
}

function checkSourceEvidence(candidate, label) {
  if (candidate.id === 'alembic') {
    const evidence = requireObject(candidate.sourceEvidence, `${label}.sourceEvidence`);
    if (!evidence) return;
    requireExact(evidence.fixedRef, 'rel_1_19_1', `${label}.sourceEvidence.fixedRef`);
    requireExact(evidence.searchScope, 'alembic/', `${label}.sourceEvidence.searchScope`);
    requireExact(evidence.advisoryLockSearchResult, 'no pg_advisory occurrence found in the fixed tag package scope; this is not a claim about all extensions or deployments', `${label}.sourceEvidence.advisoryLockSearchResult`);
    const paths = requireArray(evidence.sourcePaths, `${label}.sourceEvidence.sourcePaths`);
    requireExact(JSON.stringify(paths), JSON.stringify(['alembic/ddl/postgresql.py', 'alembic/runtime/migration.py', 'alembic/runtime/environment.py']), `${label}.sourceEvidence.sourcePaths`);
  }
  if (candidate.id === 'yoyo-migrations') {
    const evidence = requireObject(candidate.sourceEvidence, `${label}.sourceEvidence`);
    if (!evidence) return;
    requireExact(evidence.artifactSha256, 'fc65d3a6d9449c1c54d64ff2ff98e32a27da356057c60e3471010bfb19ede081', `${label}.sourceEvidence.artifactSha256`);
    requireExact(evidence.licensePath, 'yoyo_migrations-9.0.0.dist-info/LICENSE.txt', `${label}.sourceEvidence.licensePath`);
    const paths = requireArray(evidence.sourcePaths, `${label}.sourceEvidence.sourcePaths`);
    requireExact(JSON.stringify(paths), JSON.stringify(['yoyo/backends/base.py:523-534', 'yoyo/migrations.py:62-72']), `${label}.sourceEvidence.sourcePaths`);
  }
}

function checkCandidate(rawCandidate, index, seenIds) {
  const label = `candidates[${index}]`;
  const candidate = requireObject(rawCandidate, label);
  if (!candidate) return;
  const id = requireString(candidate.id, `${label}.id`);
  if (seenIds.has(id)) fail(`${label}.id duplicates ${id}.`);
  seenIds.add(id);
  const frozen = frozenCandidates[id];
  if (!frozen) { fail(`${label}.id is not a frozen candidate: ${id}.`); return; }

  ['name', 'capability', 'decision', 'version'].forEach((field) => requireExact(candidate[field], frozen[field], `${label}.${field}`));
  const packageInfo = requireObject(candidate.package, `${label}.package`);
  if (packageInfo) {
    ['type', 'name', 'requiresPython', 'python312Support'].forEach((field) => requireExact(packageInfo[field], frozen.package[field], `${label}.package.${field}`));
    requireFrozenUrl(packageInfo.metadataUrl, frozen.package.metadataUrl, `${label}.package.metadataUrl`);
    requireFrozenUrl(packageInfo.releaseUrl, frozen.package.releaseUrl, `${label}.package.releaseUrl`, { nullable: frozen.package.type === 'internal' });
    requireFrozenUrl(packageInfo.repositoryUrl, frozen.package.repositoryUrl, `${label}.package.repositoryUrl`);
  }

  const license = requireObject(candidate.license, `${label}.license`);
  if (license) {
    requireExact(license.spdx, frozen.license.spdx, `${label}.license.spdx`);
    requireExact(license.status, 'verified', `${label}.license.status`);
    requireFrozenUrl(license.sourceUrl, frozen.license.sourceUrl, `${label}.license.sourceUrl`);
    requireFrozenDate(license.verifiedAt, `${label}.license.verifiedAt`);
    checkFrozenDistributionConditions(license.distributionConditions, frozenDistributionConditions[id], `${label}.license.distributionConditions`);
    if (candidate.decision === 'adopted' && !allowedAdoptedLicenses.has(license.spdx)) fail(`${label} adopts unapproved license ${license.spdx}.`);
  }

  const maintenance = requireObject(candidate.maintenance, `${label}.maintenance`);
  if (maintenance) {
    requireFrozenDate(maintenance.checkedAt, `${label}.maintenance.checkedAt`);
    requireTimestamp(maintenance.latestReleaseAt, `${label}.maintenance.latestReleaseAt`, { nullable: frozen.package.type === 'internal' });
    if (frozen.package.type === 'internal') requireExact(maintenance.latestReleaseAt, null, `${label}.maintenance.latestReleaseAt`);
    if (typeof maintenance.repositoryArchived !== 'boolean') fail(`${label}.maintenance.repositoryArchived must be boolean.`);
    if (candidate.decision === 'adopted' && maintenance.repositoryArchived) fail(`${label} adopts an archived repository.`);
    requireArray(maintenance.signals, `${label}.maintenance.signals`).forEach((signal, signalIndex) => {
      const text = requireString(signal, `${label}.maintenance.signals[${signalIndex}]`);
      if (popularityPattern.test(text)) fail(`${label}.maintenance.signals[${signalIndex}] uses popularity evidence.`);
    });
  }

  const dependencies = requireObject(candidate.dependencies, `${label}.dependencies`);
  if (dependencies) {
    checkDirectEntries(dependencies.direct, frozenDirect[id], `${label}.dependencies.direct`);
    checkFrozenOptionalEntries(dependencies.optional, frozenOptional[id], `${label}.dependencies.optional`);
    checkSnapshotEntries(dependencies.transitiveSnapshot, `${label}.dependencies.transitiveSnapshot`, frozenAdoptedSnapshots[id] ?? null);
    checkRequiredSnapshotSubset(dependencies.transitiveSnapshot, frozenRequiredSnapshotSubsets[id], `${label}.dependencies.transitiveSnapshot`);
    const lockStatus = requireString(dependencies.lockStatus, `${label}.dependencies.lockStatus`);
    if (candidate.decision !== 'adopted' && !/(deferred|rejected|unresolved|no .*lock|not .*locked)/i.test(lockStatus)) fail(`${label}.dependencies.lockStatus must state the unresolved lock boundary.`);
  }

  checkRationale(candidate.rationale, `${label}.rationale`);
  checkFrozenAlternatives(candidate.alternatives, frozenAlternatives[id], `${label}.alternatives`);
  const boundary = requireString(candidate.boundary, `${label}.boundary`);
  requireExact(boundary, frozenBoundaries[id], `${label}.boundary`);
  const fallback = requireString(candidate.fallback, `${label}.fallback`);
  requireExact(fallback, frozenFallbacks[id], `${label}.fallback`);
  if (candidate.decision === 'adopted' && boundary.length < 40) fail(`${label}.boundary is too vague.`);
  const blockers = requireArray(candidate.blockers, `${label}.blockers`, { allowEmpty: true });
  if (candidate.decision === 'adopted' && blockers.length > 0) fail(`${label} is adopted with unresolved blockers.`);
  requireExact(candidate.starPolicy, 'not_a_decision_factor', `${label}.starPolicy`);
  checkSourceEvidence(candidate, label);
}

function checkSelections(rawSelections, candidates) {
  const selections = requireObject(rawSelections, 'selectedByCapability');
  if (!selections) return;
  requireExact(Object.keys(selections).length, Object.keys(frozenSelections).length, 'selectedByCapability key count');
  const values = Object.values(selections);
  if (new Set(values).size !== values.length) fail('selectedByCapability must select five unique candidates.');
  const candidateById = new Map(candidates.filter(isObject).map((candidate) => [candidate.id, candidate]));
  Object.entries(frozenSelections).forEach(([capability, expectedId]) => {
    requireExact(selections[capability], expectedId, `selectedByCapability.${capability}`);
    const candidate = candidateById.get(selections[capability]);
    if (!candidate) fail(`selectedByCapability.${capability} references an unknown candidate.`);
    else {
      requireExact(candidate.capability, capability, `selectedByCapability.${capability} candidate capability`);
      requireExact(candidate.decision, 'adopted', `selectedByCapability.${capability} candidate decision`);
    }
  });
  Object.keys(selections).forEach((key) => { if (!(key in frozenSelections)) fail(`selectedByCapability has unexpected key ${key}.`); });
}

function canonicalReportRow(id, frozen) {
  return `| \`${id}\` | ${frozen.decision} | \`${frozen.version}\` | \`${frozen.capability}\` | ${frozen.license.spdx} |`;
}
function checkReportText(report) {
  for (const [id, frozen] of Object.entries(frozenCandidates)) {
    const rows = report.split(/\r?\n/).filter((line) => {
      const match = line.match(/^\s*\|\s*`([^`]+)`\s*\|/);
      return match?.[1] === id;
    });
    if (rows.length !== 1) { fail(`runtime dependency report must contain exactly one matrix row for ${id}.`); continue; }
    requireExact(rows[0].trim(), canonicalReportRow(id, frozen), `runtime dependency report row ${id}`);
  }
}
function checkReport() {
  if (!fs.existsSync(reportPath)) { fail('runtime dependency decision report is missing.'); return; }
  checkReportDocument(fs.readFileSync(reportPath, 'utf8'));
}
function checkReportDocument(report) {
  if (!report.includes(frozenReportClaim)) fail('runtime dependency report is missing the frozen claim boundary.');
  report.split(/\r?\n/).forEach((line, index) => {
    if (hasPositiveClaim(line)) fail(`runtime dependency report line ${index + 1} falsely claims a reviewed candidate or runtime stack is installed, running, validated, or production-ready.`);
  });
  checkReportText(report);
}

function validateAudit(audit, { includeReport = true } = {}) {
  failures.length = 0;
  if (!isObject(audit)) { fail('audit root must be an object.'); return [...failures]; }
  requireExact(audit.schemaVersion, 1, 'schemaVersion');
  requireTimestamp(audit.generatedAt, 'generatedAt');
  if (typeof audit.generatedAt === 'string' && Date.parse(audit.generatedAt) < Date.parse(frozenAt)) fail('generatedAt cannot be stale relative to the frozen contract timestamp.');
  requireTimestamp(audit.frozenAt, 'frozenAt');
  requireExact(audit.frozenAt, frozenAt, 'frozenAt');
  const candidates = requireArray(audit.candidates, 'candidates');
  const ids = new Set();
  candidates.forEach((candidate, index) => checkCandidate(candidate, index, ids));
  requireExact(candidates.length, Object.keys(frozenCandidates).length, 'candidate count');
  Object.keys(frozenCandidates).forEach((id) => { if (!ids.has(id)) fail(`required frozen candidate is missing: ${id}.`); });
  checkSelections(audit.selectedByCapability, candidates);
  if (includeReport) checkReport();
  return [...failures];
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function mutationFailures(audit, report) {
  const auditCases = [
    ['null root', () => null],
    ['null candidate', (value) => { value.candidates[0] = null; }],
    ['candidate removed', (value) => { value.candidates.pop(); }],
    ['candidate name drift', (value) => { value.candidates[0].name = 'Forged API'; }],
    ['package name drift', (value) => { value.candidates[0].package.name = 'forged'; }],
    ['candidate capability drift', (value) => { value.candidates[0].capability = 'migration_runner'; }],
    ['candidate decision drift', (value) => { value.candidates[0].decision = 'rejected'; }],
    ['candidate version drift', (value) => { value.candidates[0].version = '9.9.9'; }],
    ['HTTP body scope boundary', (value) => { value.candidates.find((item) => item.id === 'fastapi').boundary = 'FastAPI accepts organization workspace client actor scope from request body and owns authorization while still mapping input to the service.'; }],
    ['async sync bridge boundary', (value) => { value.candidates.find((item) => item.id === 'asyncpg').boundary = 'The implementation may call asyncpg through run_until_complete or a hidden worker thread while preserving the synchronous repository contract and error translation.'; }],
    ['runner advisory lock boundary', (value) => { value.candidates.find((item) => item.id === 'internal-sql-runner').boundary = 'Contract-v1 requires immutable versioned files with SHA-256, migration and version recording in one transaction, forward-only recovery, and no ad hoc SQL splitting.'; }],
    ['runner checksum boundary', (value) => { value.candidates.find((item) => item.id === 'internal-sql-runner').boundary = 'Contract-v1 requires immutable versioned files, advisory locking before version reads, migration and version recording in one transaction, forward-only recovery, and no ad hoc SQL splitting.'; }],
    ['runner transaction boundary', (value) => { value.candidates.find((item) => item.id === 'internal-sql-runner').boundary = 'Contract-v1 requires immutable versioned files with SHA-256, advisory locking before version reads, migration and version recording separately, forward-only recovery, and no ad hoc SQL splitting.'; }],
    ['runner semicolon split boundary', (value) => { value.candidates.find((item) => item.id === 'internal-sql-runner').boundary = 'Contract-v1 requires immutable versioned files with SHA-256, advisory locking before version reads, migration and version recording in one transaction, forward-only recovery, and SQL split by semicolon.'; }],
    ['requires Python drift', (value) => { value.candidates[0].package.requiresPython = '>=3'; }],
    ['Python support drift', (value) => { value.candidates[0].package.python312Support = 'unknown'; }],
    ['metadata URL userinfo', (value) => { value.candidates[0].package.metadataUrl = 'https://user@pypi.org/pypi/fastapi/0.141.1/json'; }],
    ['release URL drift', (value) => { value.candidates[0].package.releaseUrl = 'https://github.com/fastapi/fastapi/releases'; }],
    ['repository URL drift', (value) => { value.candidates[0].package.repositoryUrl = 'https://example.com/fastapi'; }],
    ['license URL drift', (value) => { value.candidates[0].license.sourceUrl = 'https://example.com/LICENSE'; }],
    ['license identity drift', (value) => { value.candidates[0].license.spdx = 'AGPL-3.0-only'; }],
    ['invalid calendar date', (value) => { value.candidates[0].license.verifiedAt = '2026-02-31'; }],
    ['future verified date', (value) => { value.candidates[0].license.verifiedAt = '2099-01-01'; }],
    ['object distribution condition', (value) => { value.candidates[0].license.distributionConditions = [{}]; }],
    ['duplicate direct dependency', (value) => { value.candidates[0].dependencies.direct.push(clone(value.candidates[0].dependencies.direct[0])); }],
    ['missing direct dependency', (value) => { value.candidates.find((item) => item.id === 'litestar').dependencies.direct.pop(); }],
    ['missing frozen optional extra', (value) => { value.candidates.find((item) => item.id === 'uvicorn').dependencies.optional = []; }],
    ['missing adopted snapshot package', (value) => { value.candidates[0].dependencies.transitiveSnapshot.pop(); }],
    ['snapshot version drift', (value) => { value.candidates[0].dependencies.transitiveSnapshot[0].version = '1.5.0'; }],
    ['snapshot license drift', (value) => { value.candidates[0].dependencies.transitiveSnapshot[0].license = 'AGPL-3.0-only'; }],
    ['snapshot source drift', (value) => { value.candidates[0].dependencies.transitiveSnapshot[0].sourceUrl = 'https://example.com/json'; }],
    ['Psycopg null required snapshot', (value) => { value.candidates.find((item) => item.id === 'psycopg').dependencies.transitiveSnapshot = null; }],
    ['yoyo object required snapshot', (value) => { value.candidates.find((item) => item.id === 'yoyo-migrations').dependencies.transitiveSnapshot = {}; }],
    ['Psycopg Windows tzdata removed', (value) => { value.candidates.find((item) => item.id === 'psycopg').dependencies.direct.pop(); }],
    ['yoyo zipp removed', (value) => { value.candidates.find((item) => item.id === 'yoyo-migrations').dependencies.transitiveSnapshot = value.candidates.find((item) => item.id === 'yoyo-migrations').dependencies.transitiveSnapshot.filter((item) => item.name !== 'zipp'); }],
    ['alternative object', (value) => { value.candidates[0].alternatives = [{}]; }],
    ['alternative drift', (value) => { value.candidates[0].alternatives[0] = 'an unreviewed alternative'; }],
    ['selection missing', (value) => { delete value.selectedByCapability.http_framework; }],
    ['selection duplicated', (value) => { value.selectedByCapability.asgi_server = 'fastapi'; }],
    ['selection points to deferred', (value) => { value.selectedByCapability.http_framework = 'litestar'; }],
    ['RFC timestamp', (value) => { value.generatedAt = 'Sun, 09 Aug 2026 00:00:00 GMT'; }],
    ['future timestamp', (value) => { value.generatedAt = '2099-08-09T00:00:00Z'; }],
    ['stale generated timestamp', (value) => { value.generatedAt = '2026-01-01T00:00:00+08:00'; }],
    ['frozen contract date drift', (value) => { value.frozenAt = '2026-08-10T00:00:00+08:00'; }],
    ['popularity downloads rationale', (value) => { value.candidates[0].rationale[0].text = 'Widely used with a high download count.'; }],
    ['internal forged release', (value) => { value.candidates.find((item) => item.id === 'internal-sql-runner').package.releaseUrl = `https://github.com/Patch-A/marketops-ai-workbench/releases/tag/${baseline}`; }],
    ['internal forged release date', (value) => { value.candidates.find((item) => item.id === 'internal-sql-runner').maintenance.latestReleaseAt = '2026-08-09T00:00:00+08:00'; }],
    ['yoyo artifact hash drift', (value) => { value.candidates.find((item) => item.id === 'yoyo-migrations').sourceEvidence.artifactSha256 = '0'.repeat(64); }],
  ];
  const reportCases = [
    ['conflicting report row', (text) => `${text}\n| \`fastapi\` | rejected | \`9.9.9\` | \`http_framework\` | AGPL-3.0-only |`],
    ['report capability prose', (text) => text.replace('`http_framework`', 'HTTP framework')],
    ['duplicate report row', (text) => `${text}\n${canonicalReportRow('fastapi', frozenCandidates.fastapi)}`],
    ['forbidden installation claim', (text) => `${text}\nall dependencies are installed and production-validated`],
    ['adopted candidate installed and production-ready claim', (text) => `${text}\nFastAPI is installed and production-ready.`],
    ['selected runtime stack production-ready claim', (text) => `${text}\nThe selected runtime stack is production-ready.`],
    ['adopted Chinese candidate production claim', (text) => `${text}\nFastAPI 已安装并可生产使用。`],
    ['adopted candidate already installed claim', (text) => `${text}\nFastAPI is already installed.`],
    ['selected runtime stack ready for production claim', (text) => `${text}\nThe selected runtime stack is ready for production.`],
    ['adopted Chinese candidate ready for production claim', (text) => `${text}\nFastAPI 已经安装并可用于生产。`],
    ['mixed negative and positive production claim', (text) => `${text}\nFastAPI is not installed but is production-ready.`],
    ['production use synonym claim', (text) => `${text}\nFastAPI is ready for production use.`],
    ['versioned candidate production claim', (text) => `${text}\nFastAPI 0.141.1 is installed and production-ready.`],
    ['Chinese not-only production claim', (text) => `${text}\nFastAPI 不仅已经安装而且可用于生产。`],
    ['Markdown URL production claim', (text) => `${text}\n[FastAPI](https://example.com/docs) is installed and production-ready.`],
    ['plain URL production claim', (text) => `${text}\nFastAPI at https://example.com/docs is installed and production-ready.`],
    ['English semicolon inherited production claim', (text) => `${text}\nFastAPI is not installed; however it is production-ready.`],
    ['Chinese semicolon inherited production claim', (text) => `${text}\nFastAPI 尚未安装；但可用于生产。`],
    ['claim boundary removed', (text) => text.replace(frozenReportClaim, 'This decision approves production-ready dependencies.')],
  ];
  const allowedReportCases = [
    ['direct English negation', (text) => `${text}\nFastAPI is not installed and is not production-ready.`],
    ['runtime stack English negation', (text) => `${text}\nThe selected runtime stack is not ready for production.`],
    ['direct Chinese negation', (text) => `${text}\nFastAPI 尚未安装且不可用于生产。`],
    ['outer English claim negation', (text) => `${text}\nWe do not claim FastAPI is installed and production-ready.`],
    ['outer Chinese claim negation', (text) => `${text}\n我们不声称 FastAPI 已安装并可生产使用。`],
    ['not-yet English negation', (text) => `${text}\nFastAPI is not yet installed or validated.`],
    ['generic English outer claim negation', (text) => `${text}\nWe do not claim dependencies are installed or production-validated.`],
    ['generic Chinese outer claim negation', (text) => `${text}\n我们不声称依赖已安装或生产就绪。`],
  ];
  const missed = [];
  for (const [name, mutate] of auditCases) {
    const mutated = clone(audit);
    const result = mutate(mutated);
    if (validateAudit(result === undefined ? mutated : result, { includeReport: false }).length === 0) missed.push(`${name}: checker accepted weakened audit`);
  }
  for (const [name, mutate] of reportCases) {
    failures.length = 0;
    checkReportDocument(mutate(report));
    if (failures.length === 0) missed.push(`${name}: checker accepted weakened report`);
  }
  for (const [name, mutate] of allowedReportCases) {
    failures.length = 0;
    checkReportDocument(mutate(report));
    if (failures.length > 0) missed.push(`${name}: checker rejected an explicit negative boundary statement: ${failures.join(' | ')}`);
  }
  return { missed, count: auditCases.length + reportCases.length };
}

function main() {
  if (!fs.existsSync(auditPath)) {
    console.error('Runtime dependency admission audit is missing: validation/results/m1-01-runtime-dependency-admission.json');
    process.exit(1);
  }
  let audit;
  try { audit = JSON.parse(fs.readFileSync(auditPath, 'utf8')); }
  catch (error) { console.error(`Runtime dependency admission audit is not valid JSON: ${error.message}`); process.exit(1); }
  const validationFailures = validateAudit(audit);
  const report = fs.existsSync(reportPath) ? fs.readFileSync(reportPath, 'utf8') : '';
  const mutationResult = validationFailures.length === 0 ? mutationFailures(audit, report) : { missed: [], count: 0 };
  if (validationFailures.length > 0 || mutationResult.missed.length > 0) {
    console.error([...validationFailures, ...mutationResult.missed].join('\n'));
    process.exit(1);
  }
  console.log(`Runtime dependency admission passed: ${audit.candidates.length} frozen candidates, ${Object.keys(frozenSelections).length} unique capability selections, and ${mutationResult.count} weakening mutations.`);
}

main();
