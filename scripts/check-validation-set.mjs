import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const mode = process.argv[2] ?? 'public';
const publicManifestPath = path.join(root, 'validation', 'manifest.json');
const publicCaseManifestPath = path.join(root, 'validation', 'public-cases', 'manifest.json');
const privateManifestPath = path.join(root, 'validation', 'private', 'manifest.json');
const requiredCoverage = ['proposal', 'schedule', 'change', 'retrospective'];

function fail(message) {
  console.error(`Validation set check failed: ${message}`);
  process.exit(1);
}

function loadJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    fail(`cannot read ${path.relative(root, file)}: ${error.message}`);
  }
}

function validateManifest(manifest, source, { isPrivate = false } = {}) {
  if (manifest.schemaVersion !== 1 || !Array.isArray(manifest.samples)) fail(`${source} must use schemaVersion 1 and contain samples.`);
  for (const sample of manifest.samples) {
    if (!sample.id || !['synthetic', 'historical'].includes(sample.type) || !sample.title) fail(`${source} contains an invalid sample.`);
    if (sample.dataUse?.status !== 'approved' || !sample.dataUse.approvedAt || !sample.dataUse.approvedByRole) fail(`${sample.id} lacks approved data-use metadata.`);
    if (!Array.isArray(sample.dataUse.allowedUses) || sample.dataUse.allowedUses.length === 0) fail(`${sample.id} has no allowed use.`);
    if (sample.dataUse.containsPersonalData !== false) fail(`${sample.id} contains or has an unknown personal-data status.`);
    if (sample.anonymization?.containsDirectIdentifiers !== false || !sample.anonymization.reviewedAt) fail(`${sample.id} lacks an identifier review.`);
    if (sample.type === 'historical' && sample.anonymization.status !== 'passed') fail(`${sample.id} historical material has not passed anonymization review.`);
    if (sample.type === 'historical' && sample.publishable !== false) fail(`${sample.id} historical material must default to non-publishable.`);
    if (!isPrivate && sample.type === 'historical') fail(`${sample.id} historical material cannot be listed in the public manifest.`);
    for (const coverage of requiredCoverage) if (sample.coverage?.[coverage] !== true) fail(`${sample.id} does not cover ${coverage}.`);
    if (!Array.isArray(sample.files) || sample.files.length < 4) fail(`${sample.id} must reference at least four project files.`);
    for (const reference of sample.files) {
      const normalized = reference.replaceAll('\\', '/');
      if (!normalized.startsWith('validation/')) fail(`${sample.id} file is outside validation/: ${reference}`);
      if (!isPrivate && normalized.startsWith('validation/private/')) fail(`${sample.id} public record references private data.`);
      if (!fs.existsSync(path.resolve(root, reference))) fail(`${sample.id} references missing file ${reference}.`);
    }
  }
  return manifest.samples;
}

function validateFixture(samples) {
  const synthetic = samples.filter((sample) => sample.type === 'synthetic');
  if (synthetic.length < 1) fail('at least one synthetic project is required.');
  const fixture = synthetic[0];
  const groundTruthRef = fixture.files.find((reference) => reference.endsWith('ground-truth.json'));
  const scheduleRef = fixture.files.find((reference) => reference.endsWith('.csv'));
  if (!groundTruthRef || !scheduleRef) fail(`${fixture.id} needs ground truth and a CSV schedule.`);

  const groundTruth = loadJson(path.resolve(root, groundTruthRef));
  if (groundTruth.fixtureId !== fixture.id || groundTruth.fixtureType !== 'synthetic') fail(`${fixture.id} ground truth identity does not match.`);
  const schedule = fs.readFileSync(path.resolve(root, scheduleRef), 'utf8');
  for (const taskId of groundTruth.requiredTaskIds ?? []) if (!schedule.includes(taskId)) fail(`${fixture.id} schedule is missing ${taskId}.`);
  for (const taskId of groundTruth.lockedTaskIds ?? []) {
    const row = schedule.split(/\r?\n/).find((line) => line.startsWith(`${taskId},`));
    if (!row || !row.includes(',true,')) fail(`${fixture.id} locked task ${taskId} is not marked locked.`);
  }
}

function validatePublicCases(manifest) {
  if (manifest.schemaVersion !== 1 || !Array.isArray(manifest.cases)) fail('public case manifest must use schemaVersion 1 and contain cases.');
  if (manifest.cases.length < 2) fail('at least two public case reconstructions are required.');
  const dimensions = new Set();
  for (const item of manifest.cases) {
    if (!item.id || item.type !== 'public_case_reconstruction' || !item.title) fail('public case manifest contains an invalid case.');
    if (!Number.isInteger(item.eventYear) || item.eventYear < 2024) fail(`${item.id} is not an AI-era case.`);
    if (!item.source?.publisher || !/^https:\/\//.test(item.source?.url ?? '') || !/^\d{4}-\d{2}-\d{2}$/.test(item.source?.accessedAt ?? '')) fail(`${item.id} lacks source provenance.`);
    if (!Array.isArray(item.aiEraDimensions) || item.aiEraDimensions.length < 3) fail(`${item.id} lacks AI-era workflow dimensions.`);
    item.aiEraDimensions.forEach((dimension) => dimensions.add(dimension));
    if (!Array.isArray(item.facts) || item.facts.length < 3) fail(`${item.id} lacks enough source-labelled facts.`);
    if (!Array.isArray(item.unknowns) || item.unknowns.length < 3) fail(`${item.id} does not expose enough unknowns.`);
    const normalized = (item.file ?? '').replaceAll('\\', '/');
    if (!normalized.startsWith('validation/public-cases/') || !fs.existsSync(path.resolve(root, normalized))) fail(`${item.id} reconstruction file is missing or outside validation/public-cases/.`);
    const text = fs.readFileSync(path.resolve(root, normalized), 'utf8');
    for (const marker of ['PUBLIC CASE RECONSTRUCTION', '已确认的公开描述', '合理重构', '无法验证', '禁止用途']) {
      if (!text.includes(marker)) fail(`${item.id} reconstruction is missing marker ${marker}.`);
    }
    for (const fact of item.facts) if (!text.includes(fact)) fail(`${item.id} reconstruction is missing ${fact}.`);
  }
  for (const required of ['late_stage_change', 'human_approval', 'behavioral_instrumentation', 'evidence_based_iteration']) {
    if (!dimensions.has(required)) fail(`public case set lacks AI-era dimension ${required}.`);
  }
  return manifest.cases;
}

if (!['public', 'full'].includes(mode)) fail('use public or full mode.');
if (!fs.existsSync(publicManifestPath)) fail('validation/manifest.json is missing.');
if (!fs.existsSync(publicCaseManifestPath)) fail('validation/public-cases/manifest.json is missing.');

const publicSamples = validateManifest(loadJson(publicManifestPath), 'validation/manifest.json');
validateFixture(publicSamples);
const publicCases = validatePublicCases(loadJson(publicCaseManifestPath));

if (mode === 'full') {
  if (!fs.existsSync(privateManifestPath)) fail('validation/private/manifest.json is missing; add two approved anonymized historical projects locally.');
  const privateSamples = validateManifest(loadJson(privateManifestPath), 'validation/private/manifest.json', { isPrivate: true });
  if (privateSamples.filter((sample) => sample.type === 'historical').length < 2) fail('full validation requires at least two historical projects.');
  console.log(`Full validation set is valid: ${publicSamples.length} synthetic, ${publicCases.length} public-case, and ${privateSamples.length} private samples.`);
} else {
  console.log(`Public validation set is valid: ${publicSamples.length} synthetic sample and ${publicCases.length} AI-era public-case reconstructions.`);
}
