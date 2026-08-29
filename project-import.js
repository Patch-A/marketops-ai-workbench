(function projectImportModule(globalScope) {
  'use strict';

  const IMPORT_ROUTE = '/v1/project-imports';
  const PROJECTS_ROUTE = '/v1/projects';
  const MODEL_PROFILES_ROUTE = '/v1/model-profiles';
  const MODEL_MATCH_ROUTE = '/v1/model-task-matches';
  const WORKBENCH_BRIEFS_ROUTE = '/v1/workbench/briefs';
  const WORKBENCH_RESEARCH_RUNS_ROUTE = '/v1/workbench/research-runs';
  const WORKBENCH_PROPOSAL_DRAFTS_ROUTE = '/v1/workbench/proposal-drafts';
  const GEO_QUERY_SETS_ROUTE = '/v1/workbench/geo/query-sets';
  const CONTENT_BRIEFS_ROUTE = '/v1/workbench/content/briefs';
  const CONTENT_ASSETS_ROUTE = '/v1/workbench/content/assets';
  const CALENDAR_ITEMS_ROUTE = '/v1/workbench/calendar/items';
  const OBSIDIAN_CONNECTION_ROUTE = '/v1/workbench/obsidian/connection';
  const OBSIDIAN_NOTES_ROUTE = '/v1/workbench/obsidian/notes';
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const SOURCE_EXTENSIONS = new Set(['md', 'markdown', 'csv', 'docx']);
  const PROPOSAL_EXTENSIONS = new Set(['md', 'markdown', 'docx']);
  const PROJECT_STATUSES = new Set(['planning', 'active', 'archived']);
  const REVIEW_KINDS = new Set(['deliverable', 'milestone', 'constraint', 'assumption']);
  const REVIEW_CLASSIFICATIONS = new Set(['fact', 'hypothesis']);
  const REVIEW_ACTIONS = new Set(['approve', 'modify', 'reject']);
  const REVIEW_STATUSES = new Set(['pending', 'approve', 'modify', 'reject']);
  const CANONICAL_MEDIA_TYPES = Object.freeze({
    md: 'text/markdown',
    markdown: 'text/markdown',
    csv: 'text/csv',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  });

  class ProjectApiError extends Error {
    constructor(code, message, options = {}) {
      super(message);
      this.name = 'ProjectApiError';
      this.code = code;
      this.status = options.status || 0;
      this.retryable = options.retryable === true;
      this.uncertain = options.uncertain === true;
      this.requestId = typeof options.requestId === 'string' ? options.requestId : '';
    }
  }

  function extensionOf(name) {
    if (typeof name !== 'string') return '';
    const trimmed = name.trim();
    if (!trimmed || !trimmed.includes('.')) return '';
    return trimmed.split('.').pop().toLowerCase();
  }

  function isSupportedImportName(name) {
    return SOURCE_EXTENSIONS.has(extensionOf(name));
  }

  function isSupportedProposalName(name) {
    return PROPOSAL_EXTENSIONS.has(extensionOf(name));
  }

  function normalizeUpload(file) {
    if (!file || typeof file.name !== 'string' || typeof file.slice !== 'function') {
      throw new ProjectApiError('INVALID_INPUT', 'A readable upload file is required.');
    }
    const extension = extensionOf(file.name);
    const mediaType = CANONICAL_MEDIA_TYPES[extension];
    if (!mediaType) throw new ProjectApiError('UNSUPPORTED_FORMAT', 'The selected file format is unsupported.');
    return {
      body: file.slice(0, file.size, mediaType),
      filename: file.name,
      mediaType,
    };
  }

  function createIdempotencyKey(cryptoApi = globalScope.crypto) {
    if (!cryptoApi?.randomUUID) {
      throw new ProjectApiError('CLIENT_CAPABILITY_REQUIRED', 'This browser cannot create a safe request key.');
    }
    return `browser-${cryptoApi.randomUUID()}`;
  }

  function importFingerprint(input) {
    const source = input?.sourceFile;
    const proposal = input?.proposalFile;
    return JSON.stringify([
      typeof input?.projectName === 'string' ? input.projectName.trim() : '',
      Number(input?.proposalVersion),
      input?.approvalConfirmed === true,
      fileFingerprint(source),
      fileFingerprint(proposal),
    ]);
  }

  function fileFingerprint(file) {
    if (!file) return null;
    return [file.name, Number(file.size), Number(file.lastModified || 0)];
  }

  function createRetryKeyManager(keyFactory = createIdempotencyKey) {
    let current = null;
    return {
      get(fingerprint) {
        if (typeof fingerprint !== 'string' || fingerprint === '') {
          throw new ProjectApiError('INVALID_INPUT', 'An import fingerprint is required.');
        }
        if (!current || current.fingerprint !== fingerprint) {
          current = { fingerprint, key: keyFactory() };
        }
        return current.key;
      },
      clear() {
        current = null;
      },
      peek() {
        return current ? { ...current } : null;
      },
    };
  }

  function createProjectApiClient(options = {}) {
    const fetchImpl = options.fetchImpl || globalScope.fetch;
    const FormDataImpl = options.FormDataImpl || globalScope.FormData;
    if (typeof fetchImpl !== 'function' || typeof FormDataImpl !== 'function') {
      throw new ProjectApiError('CLIENT_CAPABILITY_REQUIRED', 'The Server API client is unavailable.');
    }

    return {
      async listBriefs(request = {}) {
        const payload = await requestJson(fetchImpl, WORKBENCH_BRIEFS_ROUTE, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !Array.isArray(payload.briefs)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Brief list violated its contract.');
        return { briefs: payload.briefs.map(validateWorkbenchBrief) };
      },

      async createBrief(input, request = {}) {
        const body = validateWorkbenchBriefInput(input);
        const payload = await requestJson(fetchImpl, WORKBENCH_BRIEFS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.brief)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Brief response violated its contract.', { uncertain: true });
        return validateWorkbenchBrief(payload.brief);
      },

      async createResearchRun(input, request = {}) {
        const body = validateResearchRunInput(input);
        const payload = await requestJson(fetchImpl, WORKBENCH_RESEARCH_RUNS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.researchRun)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The research response violated its contract.', { uncertain: true });
        return validateResearchRun(payload.researchRun);
      },

      async createProposalDraft(input, request = {}) {
        if (!isPlainObject(input) || !UUID_PATTERN.test(input.briefId || '') || !UUID_PATTERN.test(input.researchRunId || '')) throw new ProjectApiError('INVALID_INPUT', 'The proposal draft inputs are incomplete.');
        const payload = await requestJson(fetchImpl, WORKBENCH_PROPOSAL_DRAFTS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ briefId: input.briefId, researchRunId: input.researchRunId }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.proposalDraft)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The proposal draft response violated its contract.', { uncertain: true });
        return validateProposalDraft(payload.proposalDraft);
      },

      async decideProposalDraft(draftId, input, request = {}) {
        if (!UUID_PATTERN.test(draftId || '') || !isPlainObject(input) || !positiveInteger(input.expectedVersion) || !['approve', 'revise', 'reject'].includes(input.action) || !boundedText(input.reason, 2000)) throw new ProjectApiError('INVALID_INPUT', 'The proposal decision is incomplete.');
        const payload = await requestJson(fetchImpl, `${WORKBENCH_PROPOSAL_DRAFTS_ROUTE}/${encodeURIComponent(draftId)}/decisions`, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(input), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.proposalDraft)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The proposal decision response violated its contract.', { uncertain: true });
        return validateProposalDraft(payload.proposalDraft);
      },

      async listGeoQuerySets(request = {}) {
        const payload = await requestJson(fetchImpl, GEO_QUERY_SETS_ROUTE, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !Array.isArray(payload.querySets)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO query set list violated its contract.');
        return { querySets: payload.querySets.map(validateGeoQuerySet) };
      },

      async createGeoQuerySet(input, request = {}) {
        const body = validateGeoQuerySetInput(input);
        const payload = await requestJson(fetchImpl, GEO_QUERY_SETS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.querySet)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO query set response violated its contract.', { uncertain: true });
        return validateGeoQuerySet(payload.querySet);
      },

      async listGeoSnapshots(querySetId, request = {}) {
        if (!UUID_PATTERN.test(querySetId || '')) throw new ProjectApiError('INVALID_INPUT', 'A GEO query set id is required.');
        const payload = await requestJson(fetchImpl, `${GEO_QUERY_SETS_ROUTE}/${encodeURIComponent(querySetId)}/snapshots`, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !Array.isArray(payload.snapshots) || !Array.isArray(payload.tasks)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO snapshot list violated its contract.');
        const snapshots = payload.snapshots.map(validateGeoSnapshot);
        const tasks = payload.tasks.map(validateGeoTask);
        if (snapshots.some((item) => item.querySetId !== querySetId) || tasks.some((item) => item.querySetId !== querySetId)) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO snapshot list crossed query-set scope.', { uncertain: true });
        }
        return { snapshots, tasks };
      },

      async createGeoSnapshot(querySetId, input, request = {}) {
        if (!UUID_PATTERN.test(querySetId || '')) throw new ProjectApiError('INVALID_INPUT', 'A GEO query set id is required.');
        const body = validateGeoSnapshotInput(input);
        const payload = await requestJson(fetchImpl, `${GEO_QUERY_SETS_ROUTE}/${encodeURIComponent(querySetId)}/snapshots`, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.snapshot)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO snapshot response violated its contract.', { uncertain: true });
        const snapshot = validateGeoSnapshot(payload.snapshot);
        const task = payload.task === null ? null : validateGeoTask(payload.task);
        if (snapshot.querySetId !== querySetId || (task && task.querySetId !== querySetId)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO snapshot response crossed query-set scope.', { uncertain: true });
        return { snapshot, task };
      },

      async listContentBriefs(request = {}) {
        const payload = await requestJson(fetchImpl, CONTENT_BRIEFS_ROUTE, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !Array.isArray(payload.briefs)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content Brief list violated its contract.');
        return { briefs: payload.briefs.map(validateContentBrief) };
      },
      async createContentBrief(input, request = {}) {
        const body = validateContentBriefInput(input);
        const payload = await requestJson(fetchImpl, CONTENT_BRIEFS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.brief)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content Brief response violated its contract.', { uncertain: true });
        return validateContentBrief(payload.brief);
      },
      async approveContentBrief(briefId, expectedVersion, request = {}) {
        if (!UUID_PATTERN.test(briefId || '') || !positiveInteger(expectedVersion)) throw new ProjectApiError('INVALID_INPUT', 'A Brief id and version are required.');
        const payload = await requestJson(fetchImpl, CONTENT_BRIEFS_ROUTE + '/' + encodeURIComponent(briefId) + '/approve', { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedVersion }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.brief)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Brief approval response violated its contract.', { uncertain: true });
        return validateContentBrief(payload.brief);
      },
      async listContentAssets(request = {}) {
        const payload = await requestJson(fetchImpl, CONTENT_ASSETS_ROUTE, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !Array.isArray(payload.assets)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content asset list violated its contract.');
        return { assets: payload.assets.map(validateContentAsset) };
      },
      async createContentAsset(input, request = {}) {
        if (!isPlainObject(input) || !UUID_PATTERN.test(input.briefId || '') || !boundedText(input.title, 300) || !boundedText(input.channel, 100) || !boundedText(input.format, 100) || !['content', 'image'].includes(input.assetType) || (input.assetType === 'image' && !boundedText(input.prompt, 2000))) throw new ProjectApiError('INVALID_INPUT', 'The content asset inputs are incomplete.');
        const payload = await requestJson(fetchImpl, CONTENT_ASSETS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ briefId: input.briefId, title: input.title.trim(), channel: input.channel.trim(), format: input.format.trim(), assetType: input.assetType, ...(input.prompt ? { prompt: input.prompt.trim() } : {}) }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.asset)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content asset response violated its contract.', { uncertain: true });
        return validateContentAsset(payload.asset);
      },
      async updateContentAsset(assetId, input, request = {}) {
        if (!UUID_PATTERN.test(assetId || '') || !isPlainObject(input) || !positiveInteger(input.expectedVersion) || !['draft', 'needs_authorization', 'queued', 'ready', 'failed'].includes(input.status)) throw new ProjectApiError('INVALID_INPUT', 'The content asset update is invalid.');
        const payload = await requestJson(fetchImpl, CONTENT_ASSETS_ROUTE + '/' + encodeURIComponent(assetId), { method: 'PATCH', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedVersion: input.expectedVersion, status: input.status }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.asset)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content asset update response violated its contract.', { uncertain: true });
        return validateContentAsset(payload.asset);
      },
      async listCalendarItems(period = 'all', request = {}) {
        if (!['week', 'month', 'all'].includes(period)) throw new ProjectApiError('INVALID_INPUT', 'The calendar period is invalid.');
        const payload = await requestJson(fetchImpl, CALENDAR_ITEMS_ROUTE + '?period=' + encodeURIComponent(period), { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !Array.isArray(payload.items)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The calendar list violated its contract.');
        return { items: payload.items.map(validateCalendarItem) };
      },
      async createCalendarItem(input, request = {}) {
        if (!isPlainObject(input) || !isCalendarDate(input.date) || !boundedText(input.title, 200) || !boundedText(input.source, 100) || typeof input.note !== 'string' || input.note.length > 1000) throw new ProjectApiError('INVALID_INPUT', 'The calendar item is incomplete.');
        const payload = await requestJson(fetchImpl, CALENDAR_ITEMS_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ title: input.title.trim(), date: input.date, source: input.source.trim(), note: input.note.trim() }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.item)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The calendar item response violated its contract.', { uncertain: true });
        return validateCalendarItem(payload.item);
      },
      async updateCalendarItem(itemId, input, request = {}) {
        if (!UUID_PATTERN.test(itemId || '') || !isPlainObject(input) || !positiveInteger(input.expectedVersion) || !['draft', 'confirmed'].includes(input.status)) throw new ProjectApiError('INVALID_INPUT', 'The calendar update is invalid.');
        const payload = await requestJson(fetchImpl, CALENDAR_ITEMS_ROUTE + '/' + encodeURIComponent(itemId), { method: 'PATCH', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedVersion: input.expectedVersion, status: input.status }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.item)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The calendar update response violated its contract.', { uncertain: true });
        return validateCalendarItem(payload.item);
      },
      async getObsidianConnection(request = {}) {
        const payload = await requestJson(fetchImpl, OBSIDIAN_CONNECTION_ROUTE, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || (payload.connection !== null && !isPlainObject(payload.connection))) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Obsidian connection response violated its contract.');
        return { connection: payload.connection === null ? null : validateObsidianConnection(payload.connection) };
      },
      async connectObsidian(input, request = {}) {
        if (!isPlainObject(input) || !boundedText(input.vaultPath, 500) || !Array.isArray(input.relativePaths) || input.relativePaths.length > 200 || input.relativePaths.some((value) => !boundedText(value, 500))) throw new ProjectApiError('INVALID_INPUT', 'The Obsidian connection is incomplete.');
        const payload = await requestJson(fetchImpl, OBSIDIAN_CONNECTION_ROUTE, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ vaultPath: input.vaultPath.trim(), relativePaths: input.relativePaths.map((value) => value.trim()) }), signal: request.signal }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.connection)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Obsidian connection response violated its contract.', { uncertain: true });
        return validateObsidianConnection(payload.connection);
      },
      async listObsidianNotes(request = {}) {
        const payload = await requestJson(fetchImpl, OBSIDIAN_NOTES_ROUTE, { method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal });
        if (!isPlainObject(payload) || !isPlainObject(payload.connection) || !Array.isArray(payload.notes) || payload.readOnly !== true || !isIsoDate(payload.syncedAt)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Obsidian note list violated its contract.');
        return { connection: validateObsidianConnection(payload.connection), notes: payload.notes.map(validateObsidianNote), readOnly: true, syncedAt: payload.syncedAt };
      },

      async listModelProfiles(request = {}) {
        const payload = await requestJson(fetchImpl, MODEL_PROFILES_ROUTE, {
          method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal,
        });
        if (!isPlainObject(payload) || !Array.isArray(payload.profiles)) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The model profile list violated its contract.');
        }
        return { profiles: payload.profiles.map(validateModelProfile) };
      },

      async createModelProfile(input, request = {}) {
        const body = validateModelProfileInput(input);
        const payload = await requestJson(fetchImpl, MODEL_PROFILES_ROUTE, {
          method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(body), signal: request.signal,
        }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.profile)) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The model profile response violated its contract.', { uncertain: true });
        }
        return validateModelProfile(payload.profile);
      },

      async updateModelProfile(profileId, input, request = {}) {
        if (!UUID_PATTERN.test(profileId || '')) throw new ProjectApiError('INVALID_INPUT', 'A model profile id is required.');
        const body = validateModelProfileInput({ ...input, expectedVersion: input?.expectedVersion }, true);
        const payload = await requestJson(fetchImpl, `${MODEL_PROFILES_ROUTE}/${encodeURIComponent(profileId)}`, {
          method: 'PATCH', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(body), signal: request.signal,
        }, true);
        if (!isPlainObject(payload) || !isPlainObject(payload.profile)) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The model profile response violated its contract.', { uncertain: true });
        }
        return validateModelProfile(payload.profile);
      },

      async matchModelTask(taskType, request = {}) {
        if (!nonEmptyText(taskType)) throw new ProjectApiError('INVALID_INPUT', 'A task type is required.');
        const payload = await requestJson(fetchImpl, MODEL_MATCH_ROUTE, {
          method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify({ taskType: taskType.trim() }), signal: request.signal,
        });
        if (!isPlainObject(payload) || !nonEmptyText(payload.taskType) || !Array.isArray(payload.requiredCapabilities)
          || !['matched', 'unavailable'].includes(payload.status) || !Array.isArray(payload.reasons)
          || (payload.preferred !== null && !isPlainObject(payload.preferred))
          || (payload.backup !== null && !isPlainObject(payload.backup))) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The model task match violated its contract.');
        }
        return { ...payload, preferred: payload.preferred ? validateModelProfile(payload.preferred) : null, backup: payload.backup ? validateModelProfile(payload.backup) : null };
      },

      async importProject(input, request = {}) {
        validateImportInput(input);
        if (typeof request.idempotencyKey !== 'string' || request.idempotencyKey.trim().length < 8) {
          throw new ProjectApiError('INVALID_INPUT', 'A stable idempotency key is required.');
        }
        const source = normalizeUpload(input.sourceFile);
        const proposal = normalizeUpload(input.proposalFile);
        const form = new FormDataImpl();
        form.append('projectName', input.projectName.trim());
        form.append('proposalVersion', String(input.proposalVersion));
        form.append('approvalConfirmed', 'true');
        form.append('sourceFile', source.body, source.filename);
        form.append('proposalFile', proposal.body, proposal.filename);
        const payload = await requestJson(fetchImpl, IMPORT_ROUTE, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            Accept: 'application/json',
            'Idempotency-Key': request.idempotencyKey,
          },
          body: form,
          signal: request.signal,
        }, true);
        return validateImportResult(payload);
      },

      async listProjects(request = {}) {
        const payload = await requestJson(fetchImpl, PROJECTS_ROUTE, {
          method: 'GET',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          signal: request.signal,
        });
        return validateProjectList(payload);
      },

      async getProject(projectId, request = {}) {
        if (!UUID_PATTERN.test(projectId || '')) {
          throw new ProjectApiError('INVALID_PROJECT_ID', 'The project URL is invalid.');
        }
        const payload = await requestJson(fetchImpl, `${PROJECTS_ROUTE}/${encodeURIComponent(projectId)}`, {
          method: 'GET',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          signal: request.signal,
        });
        return validateProjectDetail(payload);
      },

      async createReviewRun(projectId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !isPlainObject(input)
          || !UUID_PATTERN.test(input.expectedProposalVersionId || '')
          || !SHA256_PATTERN.test(input.expectedProposalSha256 || '')
          || typeof request.idempotencyKey !== 'string'
          || request.idempotencyKey.trim().length < 8
          || request.idempotencyKey.trim().length > 200) {
          throw new ProjectApiError('INVALID_INPUT', 'The review run request is incomplete.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/extraction-runs`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'Idempotency-Key': request.idempotencyKey.trim(),
          },
          body: JSON.stringify({
            expectedProposalVersionId: input.expectedProposalVersionId,
            expectedProposalSha256: input.expectedProposalSha256,
          }),
          signal: request.signal,
        }, true);
        const result = validateReviewCreateResult(payload);
        if (result.proposalVersionId !== input.expectedProposalVersionId
          || result.proposalSha256 !== input.expectedProposalSha256) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The review creation response did not match the requested proposal.', { uncertain: true });
        }
        return result;
      },

      async listReviewRuns(projectId, request = {}) {
        if (!UUID_PATTERN.test(projectId || '')) {
          throw new ProjectApiError('INVALID_PROJECT_ID', 'The project URL is invalid.');
        }
        const limit = request.limit === undefined ? 20 : request.limit;
        if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
          throw new ProjectApiError('INVALID_INPUT', 'The review run limit is invalid.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/extraction-runs?limit=${limit}`, {
          method: 'GET',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          signal: request.signal,
        });
        if (!isPlainObject(payload) || !hasExactKeys(payload, ['runs']) || !Array.isArray(payload.runs)) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The review run list violated its contract.');
        }
        return { runs: payload.runs.map(validateReviewRunSummary) };
      },

      async getReview(projectId, runId, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(runId || '')) {
          throw new ProjectApiError('INVALID_PROJECT_ID', 'The review URL is invalid.');
        }
        if (request.reviewVersion !== undefined
          && (!Number.isInteger(request.reviewVersion) || request.reviewVersion < 1)) {
          throw new ProjectApiError('INVALID_INPUT', 'The review version is invalid.');
        }
        const query = request.reviewVersion === undefined ? '' : `?reviewVersion=${request.reviewVersion}`;
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/extraction-runs/${encodeURIComponent(runId)}${query}`, {
          method: 'GET',
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          signal: request.signal,
        });
        const detail = validateReviewDetail(payload);
        if (detail.run.runId !== runId) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The review detail did not match the requested run.', { uncertain: true });
        }
        return detail;
      },

      async decideReview(projectId, runId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '')
          || !UUID_PATTERN.test(runId || '')
          || !isPlainObject(input)
          || !validateReviewDecisionInput(input)) {
          throw new ProjectApiError('INVALID_INPUT', 'The review decision is incomplete.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/extraction-runs/${encodeURIComponent(runId)}/decisions`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(input),
          signal: request.signal,
        }, true);
        const result = validateReviewDecisionResult(payload);
        if (result.runId !== runId
          || result.reviewVersion !== input.expectedReviewVersion + 1
          || result.decision.candidateId !== input.candidateId
          || result.decision.action !== input.action) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The review decision response did not match the requested decision.', { uncertain: true });
        }
        return result;
      },

      async createWbsPlan(projectId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !isPlainObject(input)
          || !UUID_PATTERN.test(input.reviewRunId || '') || !positiveInteger(input.reviewVersion)) {
          throw new ProjectApiError('INVALID_INPUT', 'The WBS plan request is incomplete.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans`, {
          method: 'POST', credentials: 'same-origin',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify({ reviewRunId: input.reviewRunId, reviewVersion: input.reviewVersion }),
          signal: request.signal,
        }, true);
        const result = validateWbsCreateResult(payload);
        if (result.plan.projectId !== projectId) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The WBS plan response did not match the requested project.', { uncertain: true });
        }
        return result;
      },

      async getWbsPlan(projectId, planId, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')) {
          throw new ProjectApiError('INVALID_PROJECT_ID', 'The WBS plan URL is invalid.');
        }
        let query = '';
        if (request.planVersion !== undefined) {
          if (!positiveInteger(request.planVersion)) throw new ProjectApiError('INVALID_INPUT', 'The WBS plan version is invalid.');
          query = `?planVersion=${request.planVersion}`;
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}${query}`, {
          method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal,
        });
        const plan = validateWbsPlan(payload);
        if (plan.planId !== planId || plan.projectId !== projectId) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The WBS plan did not match the requested resource.');
        }
        return plan;
      },

      async reviseWbsPlan(projectId, planId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')
          || !isPlainObject(input) || !positiveInteger(input.expectedPlanVersion)
          || !Array.isArray(input.taskUpdates) || input.taskUpdates.length < 1 || input.taskUpdates.length > 1000
          || input.taskUpdates.some((item) => !isPlainObject(item)
            || !/^candidate:[0-9a-f-]{36}$/.test(item.taskId || '') || !isPlainObject(item.changes)
            || Object.keys(item.changes).length < 1)) {
          throw new ProjectApiError('INVALID_INPUT', 'The WBS revision request is incomplete.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/revisions`, {
          method: 'POST', credentials: 'same-origin',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(input), signal: request.signal,
        }, true);
        const plan = validateWbsPlan(payload);
        if (plan.planId !== planId || plan.projectId !== projectId) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The revised WBS plan did not match the requested resource.', { uncertain: true });
        }
        return plan;
      },

      async createScheduleSnapshot(projectId, planId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')
          || !isPlainObject(input) || !positiveInteger(input.expectedPlanVersion)
          || !isCalendarDate(input.projectStart) || !Array.isArray(input.holidays)
          || input.holidays.some((day) => !isCalendarDate(day))) {
          throw new ProjectApiError('INVALID_INPUT', 'The schedule request is incomplete.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/schedule-snapshots`, {
          method: 'POST', credentials: 'same-origin',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(input), signal: request.signal,
        }, true);
        if (!isPlainObject(payload) || !hasExactKeys(payload, ['snapshot', 'replayed'])) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The schedule response violated its contract.', { uncertain: true });
        }
        const snapshot = validateScheduleSnapshot(payload.snapshot);
        if (snapshot.planId !== planId || typeof payload.replayed !== 'boolean') {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The schedule response did not match the requested plan.', { uncertain: true });
        }
        return { snapshot, replayed: payload.replayed };
      },

      async approveWbsPlan(projectId, planId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')
          || !isPlainObject(input) || !positiveInteger(input.expectedPlanVersion)
          || !UUID_PATTERN.test(input.scheduleSnapshotId || '') || !boundedText(input.reason, 1000)) {
          throw new ProjectApiError('INVALID_INPUT', 'The plan approval request is incomplete.');
        }
        const body = {
          expectedPlanVersion: input.expectedPlanVersion,
          scheduleSnapshotId: input.scheduleSnapshotId,
          reason: input.reason.trim(),
        };
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/approvals`, {
          method: 'POST', credentials: 'same-origin',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(body), signal: request.signal,
        }, true);
        const result = validatePlanApprovalCreateResult(payload);
        if (result.approval.planId !== planId
          || result.approval.planVersion !== input.expectedPlanVersion
          || result.approval.scheduleSnapshotId !== input.scheduleSnapshotId) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The plan approval did not match the requested target.', { uncertain: true });
        }
        return result;
      },

      async getWbsPlanApproval(projectId, planId, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')) {
          throw new ProjectApiError('INVALID_PROJECT_ID', 'The plan approval URL is invalid.');
        }
        let query = '';
        if (request.planVersion !== undefined) {
          if (!positiveInteger(request.planVersion)) throw new ProjectApiError('INVALID_INPUT', 'The plan approval version is invalid.');
          query = `?planVersion=${request.planVersion}`;
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/approvals${query}`, {
          method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal,
        });
        if (!isPlainObject(payload) || !hasExactKeys(payload, ['approval'])
          || (payload.approval !== null && !isPlainObject(payload.approval))) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The plan approval response violated its contract.');
        }
        const approval = payload.approval === null ? null : validatePlanApproval(payload.approval);
        if (approval && (approval.planId !== planId
          || (request.planVersion !== undefined && approval.planVersion !== request.planVersion))) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The plan approval did not match the requested resource.');
        }
        return { approval };
      },

      async getExecutionState(projectId, planId, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')
          || !positiveInteger(request.planVersion)) {
          throw new ProjectApiError('INVALID_INPUT', 'The execution state URL is invalid.');
        }
        const query = `?planVersion=${request.planVersion}`;
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/execution${query}`, {
          method: 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal,
        });
        const result = validateExecutionReadResult(payload);
        if (result.projectId !== projectId || result.planId !== planId
          || result.planVersion !== request.planVersion) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution state did not match the requested plan.');
        }
        return result;
      },

      async updateExecutionTask(projectId, planId, input, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')
          || !validateExecutionUpdateInput(input)) {
          throw new ProjectApiError('INVALID_INPUT', 'The execution update is incomplete.');
        }
        const payload = await requestJson(fetchImpl, `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/execution-updates`, {
          method: 'POST', credentials: 'same-origin',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: JSON.stringify(input), signal: request.signal,
        }, true);
        const result = validateExecutionUpdateResult(payload);
        if (result.update.taskId !== input.taskId
          || result.update.sequenceNo !== input.expectedExecutionSequence + 1
          || result.update.status !== input.status) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution update did not match the submitted task.', { uncertain: true });
        }
        return result;
      },

      async downloadExecutionExport(projectId, planId, format, request = {}) {
        if (!UUID_PATTERN.test(projectId || '') || !UUID_PATTERN.test(planId || '')
          || !['csv', 'xlsx'].includes(format) || !positiveInteger(request.planVersion)) {
          throw new ProjectApiError('INVALID_INPUT', 'The execution export URL is invalid.');
        }
        const url = `/v1/projects/${encodeURIComponent(projectId)}/wbs-plans/${encodeURIComponent(planId)}/exports/execution.${format}?planVersion=${request.planVersion}`;
        return requestDownload(fetchImpl, url, {
          method: 'GET', credentials: 'same-origin',
          headers: { Accept: format === 'csv' ? 'text/csv' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
          signal: request.signal,
        }, format);
      },
    };
  }

  async function importThenLoad(api, input, request = {}) {
    if (!api?.importProject || !api?.getProject) {
      throw new ProjectApiError('CLIENT_CAPABILITY_REQUIRED', 'A complete Server API client is required.');
    }
    const result = await api.importProject(input, request);
    if (typeof request.onProjectId === 'function') request.onProjectId(result.projectId);
    try {
      const detail = await api.getProject(result.projectId, { signal: request.signal });
      return { result, detail };
    } catch (error) {
      if (error instanceof ProjectApiError) error.uncertain = true;
      throw error;
    }
  }

  async function loadInitialProject(api, projectId, request = {}) {
    if (!api?.listProjects || !api?.getProject) {
      throw new ProjectApiError('CLIENT_CAPABILITY_REQUIRED', 'A complete Server API client is required.');
    }
    if (projectId) return api.getProject(projectId, request);
    const list = await api.listProjects(request);
    if (list.items.length === 0) return null;
    const selectedId = list.items[0].projectId;
    if (typeof request.onProjectId === 'function') request.onProjectId(selectedId);
    return api.getProject(selectedId, request);
  }

  async function requestJson(fetchImpl, url, options, commitMayBeUncertain = false) {
    let response;
    try {
      response = await fetchImpl(url, options);
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new ProjectApiError('REQUEST_CANCELLED', 'The request was cancelled.', {
          retryable: true,
          uncertain: commitMayBeUncertain,
        });
      }
      throw new ProjectApiError('NETWORK_ERROR', 'The Server API could not be reached.', {
        retryable: true,
        uncertain: commitMayBeUncertain,
      });
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The Server API returned an invalid response.', {
        status: response.status,
        retryable: response.status >= 500,
        uncertain: commitMayBeUncertain && response.ok,
      });
    }
    if (!response.ok) throw responseError(response.status, payload);
    if (!isPlainObject(payload)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The Server API returned an invalid response.', {
        status: response.status,
        uncertain: commitMayBeUncertain,
      });
    }
    return payload;
  }

  async function requestDownload(fetchImpl, url, options, format) {
    let response;
    try {
      response = await fetchImpl(url, options);
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new ProjectApiError('REQUEST_CANCELLED', 'The export request was cancelled.', { retryable: true });
      }
      throw new ProjectApiError('NETWORK_ERROR', 'The execution export could not be reached.', { retryable: true });
    }
    if (!response.ok) {
      let payload;
      try { payload = await response.json(); } catch { payload = null; }
      throw responseError(response.status, payload);
    }
    const expectedType = format === 'csv'
      ? 'text/csv'
      : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    const contentType = response.headers?.get?.('content-type') || '';
    const disposition = response.headers?.get?.('content-disposition') || '';
    const match = /^attachment; filename="([a-zA-Z0-9._-]+)"$/.exec(disposition);
    if (!contentType.toLowerCase().startsWith(expectedType) || !match || !match[1].endsWith(`.${format}`)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution export response violated its contract.');
    }
    let blob;
    try { blob = await response.blob(); } catch {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution export body was unreadable.');
    }
    return { blob, filename: match[1], format };
  }

  function responseError(status, payload) {
    const valid = isPlainObject(payload)
      && typeof payload.code === 'string'
      && typeof payload.message === 'string'
      && typeof payload.retryable === 'boolean'
      && typeof payload.requestId === 'string';
    if (!valid) {
      return new ProjectApiError('MALFORMED_RESPONSE', 'The Server API returned an invalid error response.', {
        status,
        retryable: status >= 500,
      });
    }
    return new ProjectApiError(payload.code, 'The Server API rejected the request.', {
      status,
      retryable: payload.retryable,
      requestId: payload.requestId,
    });
  }

  function validateImportInput(input) {
    if (!isPlainObject(input)
      || typeof input.projectName !== 'string'
      || input.projectName.trim().length < 1
      || input.projectName.trim().length > 300
      || !Number.isInteger(input.proposalVersion)
      || input.proposalVersion < 1
      || input.approvalConfirmed !== true
      || !isSupportedImportName(input.sourceFile?.name)
      || !isSupportedProposalName(input.proposalFile?.name)) {
      throw new ProjectApiError('INVALID_INPUT', 'The import form is incomplete or invalid.');
    }
  }

  function validateImportResult(value) {
    const idFields = ['projectId', 'sourceArtifactId', 'sourceVersionId', 'proposalArtifactId', 'proposalVersionId'];
    if (!isPlainObject(value)
      || !hasExactKeys(value, [...idFields, 'manifestSha256', 'replayed'])
      || idFields.some((field) => !UUID_PATTERN.test(value[field] || ''))
      || !SHA256_PATTERN.test(value.manifestSha256 || '')
      || typeof value.replayed !== 'boolean') {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The import response violated its contract.', { uncertain: true });
    }
    return {
      projectId: value.projectId,
      sourceArtifactId: value.sourceArtifactId,
      sourceVersionId: value.sourceVersionId,
      proposalArtifactId: value.proposalArtifactId,
      proposalVersionId: value.proposalVersionId,
      manifestSha256: value.manifestSha256,
      replayed: value.replayed,
    };
  }

  function validateProjectList(value) {
    if (!isPlainObject(value) || !hasExactKeys(value, ['projects']) || !Array.isArray(value.projects)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The project list violated its contract.');
    }
    return {
      items: value.projects.map((item) => {
        if (!isPlainObject(item)
          || !hasExactKeys(item, ['projectId', 'name', 'status', 'approvedProposalVersion', 'createdAt'])
          || !UUID_PATTERN.test(item.projectId || '')
          || !nonEmptyText(item.name)
          || !PROJECT_STATUSES.has(item.status)
          || !Number.isInteger(item.approvedProposalVersion)
          || item.approvedProposalVersion < 1
          || !isIsoDate(item.createdAt)) {
          throw new ProjectApiError('MALFORMED_RESPONSE', 'A project list item violated its contract.');
        }
        return {
          projectId: item.projectId,
          projectName: item.name,
          status: item.status,
          approvedProposalVersion: item.approvedProposalVersion,
          createdAt: item.createdAt,
        };
      }),
    };
  }

  function validateProjectDetail(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['projectId', 'name', 'status', 'createdAt', 'sourceFile', 'approvedProposal'])
      || !UUID_PATTERN.test(value.projectId || '')
      || !nonEmptyText(value.name)
      || !PROJECT_STATUSES.has(value.status)
      || !isIsoDate(value.createdAt)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The project detail violated its contract.');
    }
    return {
      projectId: value.projectId,
      projectName: value.name,
      status: value.status,
      createdAt: value.createdAt,
      source: validateArtifact(value.sourceFile, false),
      proposal: validateArtifact(value.approvedProposal, true),
    };
  }

  function validateArtifact(value, proposal) {
    const baseKeys = ['artifactId', 'versionId', 'filename', 'mediaType', 'sizeBytes'];
    const expectedKeys = proposal
      ? [...baseKeys, 'sha256', 'proposalVersion', 'approvalStatus', 'approvedAt']
      : baseKeys;
    if (!isPlainObject(value)
      || !hasExactKeys(value, expectedKeys)
      || !UUID_PATTERN.test(value.artifactId || '')
      || !UUID_PATTERN.test(value.versionId || '')
      || !nonEmptyText(value.filename)
      || !nonEmptyText(value.mediaType)
      || !Number.isInteger(value.sizeBytes)
      || value.sizeBytes < 0) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The project artifact violated its contract.');
    }
    const artifact = {
      artifactId: value.artifactId,
      versionId: value.versionId,
      filename: value.filename,
      mediaType: value.mediaType,
      sizeBytes: value.sizeBytes,
    };
    if (proposal) {
      if (!SHA256_PATTERN.test(value.sha256 || '')
        || !Number.isInteger(value.proposalVersion)
        || value.proposalVersion < 1
        || value.approvalStatus !== 'approved'
        || !isIsoDate(value.approvedAt)) {
        throw new ProjectApiError('MALFORMED_RESPONSE', 'The approved proposal violated its contract.');
      }
      artifact.proposalVersion = value.proposalVersion;
      artifact.sha256 = value.sha256;
      artifact.approvalStatus = value.approvalStatus;
      artifact.approvedAt = value.approvedAt;
    }
    return artifact;
  }

  function validateReviewCreateResult(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['runId', 'reviewVersion', 'candidateCount', 'proposalVersionId', 'proposalSha256', 'replayed', 'createdAt'])
      || !UUID_PATTERN.test(value.runId || '')
      || value.reviewVersion !== 1
      || !Number.isInteger(value.candidateCount)
      || value.candidateCount < 1
      || value.candidateCount > 1000
      || !UUID_PATTERN.test(value.proposalVersionId || '')
      || !SHA256_PATTERN.test(value.proposalSha256 || '')
      || typeof value.replayed !== 'boolean'
      || !isIsoDate(value.createdAt)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The review creation response violated its contract.', { uncertain: true });
    }
    return { ...value };
  }

  function validateReviewRunSummary(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['runId', 'proposalVersionId', 'proposalSha256', 'candidateCount', 'latestReviewVersion', 'createdAt'])
      || !UUID_PATTERN.test(value.runId || '')
      || !UUID_PATTERN.test(value.proposalVersionId || '')
      || !SHA256_PATTERN.test(value.proposalSha256 || '')
      || !Number.isInteger(value.candidateCount)
      || value.candidateCount < 1
      || value.candidateCount > 1000
      || !Number.isInteger(value.latestReviewVersion)
      || value.latestReviewVersion < 1
      || !isIsoDate(value.createdAt)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A review run item violated its contract.');
    }
    return { ...value };
  }

  function validateSourceLocation(value) {
    const fieldsByKind = {
      line_range: ['kind', 'startLine', 'endLine'],
      csv_range: ['kind', 'startRow', 'endRow'],
      docx_paragraph: ['kind', 'part', 'bodyIndex', 'paragraph'],
      docx_table: ['kind', 'part', 'bodyIndex', 'table'],
      markdown_table_cell: ['kind', 'line', 'columnIndex'],
      csv_cell: ['kind', 'row', 'columnIndex', 'columnName'],
      docx_table_cell: ['kind', 'part', 'table', 'row', 'column'],
    };
    if (!isPlainObject(value)
      || !Object.hasOwn(fieldsByKind, value.kind)
      || !hasExactKeys(value, fieldsByKind[value.kind])) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A source location violated its contract.');
    }
    const textFields = ['part', 'columnName'];
    const numericFields = fieldsByKind[value.kind].filter((key) => key !== 'kind' && !textFields.includes(key));
    if (textFields.some((key) => key in value && !nonEmptyText(value[key]))
      || numericFields.some((key) => !Number.isInteger(value[key]) || value[key] < 1)
      || (value.kind === 'line_range' && value.endLine < value.startLine)
      || (value.kind === 'csv_range' && value.endRow < value.startRow)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A source location violated its contract.');
    }
    return { ...value };
  }

  function validateReviewDecisionInput(value) {
    const required = ['expectedReviewVersion', 'candidateId', 'action', 'reason'];
    const optional = ['comment', 'replacementText'];
    const keys = Object.keys(value);
    const allowed = new Set([...required, ...optional]);
    if (required.some((key) => !Object.hasOwn(value, key))
      || keys.some((key) => !allowed.has(key))
      || !Number.isInteger(value.expectedReviewVersion)
      || value.expectedReviewVersion < 1
      || !UUID_PATTERN.test(value.candidateId || '')
      || !REVIEW_ACTIONS.has(value.action)
      || !boundedText(value.reason, 2000)
      || (value.comment !== undefined && value.comment !== null && !boundedText(value.comment, 4000))
      || (value.replacementText !== undefined && value.replacementText !== null && !boundedText(value.replacementText, 10000))) {
      return false;
    }
    const hasReplacement = typeof value.replacementText === 'string' && value.replacementText.trim() !== '';
    return (value.action === 'modify') === hasReplacement;
  }

  function validateCitation(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['sourceVersionId', 'sourceSha256', 'location', 'sectionPath', 'quote'])
      || !UUID_PATTERN.test(value.sourceVersionId || '')
      || !SHA256_PATTERN.test(value.sourceSha256 || '')
      || !Array.isArray(value.sectionPath)
      || value.sectionPath.some((item) => !nonEmptyText(item))
      || !nonEmptyText(value.quote)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A source citation violated its contract.');
    }
    return { ...value, location: validateSourceLocation(value.location) };
  }

  function validateReviewDecision(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['decisionId', 'reviewVersion', 'candidateId', 'action', 'reason', 'comment', 'replacementText', 'actorId', 'createdAt'])
      || !UUID_PATTERN.test(value.decisionId || '')
      || !Number.isInteger(value.reviewVersion)
      || value.reviewVersion < 2
      || !UUID_PATTERN.test(value.candidateId || '')
      || !REVIEW_ACTIONS.has(value.action)
      || !nonEmptyText(value.reason)
      || (value.comment !== null && !nonEmptyText(value.comment))
      || (value.replacementText !== null && !nonEmptyText(value.replacementText))
      || (value.action === 'modify') !== (value.replacementText !== null)
      || !UUID_PATTERN.test(value.actorId || '')
      || !isIsoDate(value.createdAt)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A review decision violated its contract.');
    }
    return { ...value };
  }

  function validateReviewCandidate(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['ordinal', 'candidateId', 'kind', 'text', 'classification', 'confidence', 'sourceCitation', 'review'])
      || !Number.isInteger(value.ordinal)
      || value.ordinal < 1
      || !UUID_PATTERN.test(value.candidateId || '')
      || !REVIEW_KINDS.has(value.kind)
      || !nonEmptyText(value.text)
      || !REVIEW_CLASSIFICATIONS.has(value.classification)
      || typeof value.confidence !== 'number'
      || value.confidence < 0
      || value.confidence > 1
      || !isPlainObject(value.review)
      || !hasExactKeys(value.review, ['status', 'replacementText', 'lastDecision'])
      || !REVIEW_STATUSES.has(value.review.status)
      || (value.review.replacementText !== null && !nonEmptyText(value.review.replacementText))
      || (value.review.lastDecision !== null && !isPlainObject(value.review.lastDecision))) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A review candidate violated its contract.');
    }
    return {
      ...value,
      sourceCitation: validateCitation(value.sourceCitation),
      review: {
        ...value.review,
        lastDecision: value.review.lastDecision === null ? null : validateReviewDecision(value.review.lastDecision),
      },
    };
  }

  function validateReviewDetail(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['run', 'selectedReviewVersion', 'availableReviewVersions', 'selectedDecision', 'candidates'])
      || !isPlainObject(value.run)
      || !Number.isInteger(value.selectedReviewVersion)
      || value.selectedReviewVersion < 1
      || !Array.isArray(value.availableReviewVersions)
      || value.availableReviewVersions.some((item) => !Number.isInteger(item) || item < 1)
      || (value.selectedDecision !== null && !isPlainObject(value.selectedDecision))
      || !Array.isArray(value.candidates)
      || value.candidates.length < 1
      || value.candidates.length > 1000) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The review detail violated its contract.');
    }
    const run = validateReviewRunSummary(value.run);
    const versions = value.availableReviewVersions;
    const candidates = value.candidates.map(validateReviewCandidate);
    const selectedDecision = value.selectedDecision === null ? null : validateReviewDecision(value.selectedDecision);
    const versionSequenceIsComplete = versions.every((version, index) => version === index + 1);
    const candidateIds = new Set(candidates.map((candidate) => candidate.candidateId));
    const candidatesAreConsistent = candidates.every((candidate, index) => {
      const decision = candidate.review.lastDecision;
      return candidate.ordinal === index + 1
        && candidate.sourceCitation.sourceVersionId === run.proposalVersionId
        && candidate.sourceCitation.sourceSha256 === run.proposalSha256
        && (candidate.review.status === 'modify') === (candidate.review.replacementText !== null)
        && (candidate.review.status === 'pending'
          ? decision === null
          : decision !== null
            && decision.reviewVersion <= value.selectedReviewVersion
            && decision.candidateId === candidate.candidateId
            && decision.action === candidate.review.status
            && decision.replacementText === candidate.review.replacementText);
    });
    const selectedCandidate = selectedDecision === null
      ? null
      : candidates.find((candidate) => candidate.candidateId === selectedDecision.candidateId);
    const selectedDecisionIsConsistent = value.selectedReviewVersion === 1
      ? selectedDecision === null
      : selectedDecision !== null
        && selectedDecision.reviewVersion === value.selectedReviewVersion
        && selectedCandidate !== undefined
        && selectedCandidate.review.lastDecision !== null
        && reviewDecisionsEqual(selectedCandidate.review.lastDecision, selectedDecision);
    if (!versionSequenceIsComplete
      || versions.at(-1) !== run.latestReviewVersion
      || !versions.includes(value.selectedReviewVersion)
      || run.candidateCount !== candidates.length
      || candidateIds.size !== candidates.length
      || !candidatesAreConsistent
      || !selectedDecisionIsConsistent) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The review detail was internally inconsistent.');
    }
    return {
      run,
      selectedReviewVersion: value.selectedReviewVersion,
      availableReviewVersions: [...versions],
      selectedDecision,
      candidates,
    };
  }

  function validateReviewDecisionResult(value) {
    if (!isPlainObject(value)
      || !hasExactKeys(value, ['runId', 'reviewVersion', 'decision'])
      || !UUID_PATTERN.test(value.runId || '')
      || !Number.isInteger(value.reviewVersion)
      || value.reviewVersion < 2) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The review decision response violated its contract.', { uncertain: true });
    }
    const decision = validateReviewDecision(value.decision);
    if (decision.reviewVersion !== value.reviewVersion) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The review decision response was internally inconsistent.', { uncertain: true });
    }
    return { ...value, decision };
  }

  function reviewDecisionsEqual(left, right) {
    return ['decisionId', 'reviewVersion', 'candidateId', 'action', 'reason', 'comment',
      'replacementText', 'actorId', 'createdAt'].every((key) => left[key] === right[key]);
  }

  function validateWbsCreateResult(value) {
    if (!isPlainObject(value) || !hasExactKeys(value, ['plan', 'replayed']) || typeof value.replayed !== 'boolean') {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The WBS plan response violated its contract.', { uncertain: true });
    }
    return { plan: validateWbsPlan(value.plan), replayed: value.replayed };
  }

  function validateWbsPlan(value) {
    const required = ['planId', 'projectId', 'proposalVersionId', 'proposalSha256', 'sourceReviewRunId', 'sourceReviewSnapshotId',
      'sourceReviewVersion', 'selectedPlanVersion', 'availablePlanVersions', 'status', 'planDigest', 'createdAt', 'tasks', 'controls'];
    if (!isPlainObject(value) || !hasExactKeys(value, required)
      || !UUID_PATTERN.test(value.planId || '') || !UUID_PATTERN.test(value.projectId || '')
      || !UUID_PATTERN.test(value.proposalVersionId || '') || !UUID_PATTERN.test(value.sourceReviewRunId || '')
      || !UUID_PATTERN.test(value.sourceReviewSnapshotId || '') || !SHA256_PATTERN.test(value.proposalSha256 || '')
      || !SHA256_PATTERN.test(value.planDigest || '') || !positiveInteger(value.sourceReviewVersion)
      || !positiveInteger(value.selectedPlanVersion) || !Array.isArray(value.availablePlanVersions)
      || value.availablePlanVersions.some((item) => !positiveInteger(item)) || value.status !== 'draft'
      || !isIsoDate(value.createdAt) || !Array.isArray(value.tasks) || value.tasks.length < 1
      || value.tasks.length > 1000 || !Array.isArray(value.controls) || value.controls.length > 1000) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The WBS plan violated its contract.', { uncertain: true });
    }
    return { ...value, tasks: value.tasks.map(validateWbsTask), controls: value.controls.map(validateWbsControl) };
  }

  function validateWbsTask(value) {
    const required = ['taskId', 'candidateId', 'kind', 'classification', 'sourceText', 'title', 'sourceCitation', 'reviewStatus',
      'durationWorkdays', 'predecessors', 'ownerRole', 'plannedStart', 'plannedFinish', 'hardDeadline', 'approvedBufferWorkdays', 'isLocked', 'status'];
    const statuses = new Set(['not_started', 'in_progress', 'blocked', 'completed', 'cancelled']);
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !/^candidate:[0-9a-f-]{36}$/.test(value.taskId || '')
      || !UUID_PATTERN.test(value.candidateId || '') || !['deliverable', 'milestone'].includes(value.kind)
      || !['fact', 'hypothesis'].includes(value.classification) || !nonEmptyText(value.sourceText) || !nonEmptyText(value.title)
      || !isPlainObject(value.sourceCitation) || !['approve', 'modify'].includes(value.reviewStatus)
      || !positiveInteger(value.durationWorkdays) || !Array.isArray(value.predecessors)
      || value.predecessors.some((item) => !/^candidate:[0-9a-f-]{36}$/.test(item))
      || typeof value.ownerRole !== 'string' || value.ownerRole.length > 200
      || (value.plannedStart !== null && !isCalendarDate(value.plannedStart)) || (value.plannedFinish !== null && !isCalendarDate(value.plannedFinish))
      || (value.hardDeadline !== null && !isCalendarDate(value.hardDeadline)) || !Number.isInteger(value.approvedBufferWorkdays)
      || value.approvedBufferWorkdays < 0 || typeof value.isLocked !== 'boolean' || !statuses.has(value.status)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A WBS task violated its contract.', { uncertain: true });
    }
    return { ...value };
  }

  function validateWbsControl(value) {
    const required = ['candidateId', 'kind', 'classification', 'sourceText', 'text', 'sourceCitation', 'reviewStatus'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !UUID_PATTERN.test(value.candidateId || '')
      || !['constraint', 'assumption'].includes(value.kind) || !['fact', 'hypothesis'].includes(value.classification)
      || !nonEmptyText(value.sourceText) || !nonEmptyText(value.text) || !isPlainObject(value.sourceCitation)
      || !['approve', 'modify'].includes(value.reviewStatus)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'A WBS control violated its contract.', { uncertain: true });
    }
    return { ...value };
  }

  function validateScheduleSnapshot(value) {
    const required = ['snapshotId', 'planId', 'planVersion', 'status', 'projectStart', 'holidays', 'planDigest', 'scheduleDigest',
      'createdAt', 'topologicalOrder', 'tasks', 'conflicts', 'deadlineMisses', 'sourceDateDrift'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !UUID_PATTERN.test(value.snapshotId || '')
      || !UUID_PATTERN.test(value.planId || '') || !positiveInteger(value.planVersion)
      || !['ready', 'needs_review'].includes(value.status) || !isCalendarDate(value.projectStart)
      || !Array.isArray(value.holidays) || value.holidays.some((day) => !isCalendarDate(day))
      || !SHA256_PATTERN.test(value.planDigest || '') || !SHA256_PATTERN.test(value.scheduleDigest || '')
      || !isIsoDate(value.createdAt) || !Array.isArray(value.topologicalOrder) || !Array.isArray(value.tasks)
      || value.tasks.length < 1 || !Array.isArray(value.conflicts) || !Array.isArray(value.deadlineMisses)
      || !Array.isArray(value.sourceDateDrift)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The schedule snapshot violated its contract.', { uncertain: true });
    }
    return { ...value };
  }

  function validatePlanApprovalCreateResult(value) {
    if (!isPlainObject(value) || !hasExactKeys(value, ['approval', 'replayed'])
      || typeof value.replayed !== 'boolean') {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The plan approval response violated its contract.', { uncertain: true });
    }
    return { approval: validatePlanApproval(value.approval), replayed: value.replayed };
  }

  function validatePlanApproval(value) {
    const required = ['approvalId', 'planId', 'planVersion', 'scheduleSnapshotId', 'planDigest', 'scheduleDigest', 'reason', 'approvedAt'];
    if (!isPlainObject(value) || !hasExactKeys(value, required)
      || !UUID_PATTERN.test(value.approvalId || '') || !UUID_PATTERN.test(value.planId || '')
      || !positiveInteger(value.planVersion) || !UUID_PATTERN.test(value.scheduleSnapshotId || '')
      || !SHA256_PATTERN.test(value.planDigest || '') || !SHA256_PATTERN.test(value.scheduleDigest || '')
      || !boundedText(value.reason, 1000) || value.reason !== value.reason.trim()
      || !isIsoDate(value.approvedAt)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The plan approval violated its contract.', { uncertain: true });
    }
    return { ...value };
  }

  const EXECUTION_STATUSES = new Set(['not_started', 'in_progress', 'blocked', 'completed', 'cancelled']);

  function validateExecutionReadResult(value) {
    const required = ['projectId', 'planId', 'planVersion', 'editable', 'tasks'];
    if (!isPlainObject(value) || !hasExactKeys(value, required)
      || !UUID_PATTERN.test(value.projectId || '') || !UUID_PATTERN.test(value.planId || '')
      || !positiveInteger(value.planVersion) || typeof value.editable !== 'boolean'
      || !Array.isArray(value.tasks) || value.tasks.length < 1 || value.tasks.length > 1000) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution state violated its contract.');
    }
    return { ...value, tasks: value.tasks.map(validateExecutionTask) };
  }

  function validateExecutionTask(value) {
    const required = ['taskId', 'title', 'ownerRole', 'plannedStart', 'plannedFinish', 'status',
      'blockerReason', 'actualStart', 'actualFinish', 'note', 'sequenceNo', 'updatedAt'];
    if (!isPlainObject(value) || !hasExactKeys(value, required)
      || !nonEmptyText(value.taskId) || !boundedText(value.title, 300)
      || typeof value.ownerRole !== 'string' || value.ownerRole.length > 200
      || !nullableCalendarDate(value.plannedStart) || !nullableCalendarDate(value.plannedFinish)
      || !EXECUTION_STATUSES.has(value.status) || !nullableBoundedText(value.blockerReason, 2000)
      || !nullableCalendarDate(value.actualStart) || !nullableCalendarDate(value.actualFinish)
      || !nullableBoundedText(value.note, 4000) || !Number.isInteger(value.sequenceNo) || value.sequenceNo < 0
      || (value.updatedAt !== null && !isIsoDate(value.updatedAt))) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'An execution task violated its contract.');
    }
    return { ...value };
  }

  function validateExecutionUpdateInput(value) {
    const required = ['expectedPlanVersion', 'taskId', 'expectedExecutionSequence', 'status'];
    const optional = ['blockerReason', 'actualStart', 'actualFinish', 'note'];
    if (!isPlainObject(value) || required.some((key) => !(key in value))
      || Object.keys(value).some((key) => !required.includes(key) && !optional.includes(key))
      || !positiveInteger(value.expectedPlanVersion) || !nonEmptyText(value.taskId)
      || !Number.isInteger(value.expectedExecutionSequence) || value.expectedExecutionSequence < 0
      || !EXECUTION_STATUSES.has(value.status) || !nullableBoundedText(value.blockerReason, 2000)
      || !nullableCalendarDate(value.actualStart) || !nullableCalendarDate(value.actualFinish)
      || !nullableBoundedText(value.note, 4000)) return false;
    if (value.status === 'blocked' && !boundedText(value.blockerReason, 2000)) return false;
    if (value.status !== 'blocked' && value.blockerReason !== undefined && value.blockerReason !== null) return false;
    if (value.status === 'in_progress' && !isCalendarDate(value.actualStart)) return false;
    if (value.status === 'completed' && (!isCalendarDate(value.actualStart) || !isCalendarDate(value.actualFinish))) return false;
    return !(value.actualStart && value.actualFinish && value.actualFinish < value.actualStart);
  }

  function validateExecutionUpdateResult(value) {
    if (!isPlainObject(value) || !hasExactKeys(value, ['update', 'replayed'])
      || typeof value.replayed !== 'boolean') {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution update response violated its contract.', { uncertain: true });
    }
    const update = value.update;
    const required = ['taskId', 'sequenceNo', 'status', 'blockerReason', 'actualStart', 'actualFinish', 'note', 'updatedAt'];
    if (!isPlainObject(update) || !hasExactKeys(update, required)
      || !nonEmptyText(update.taskId) || !positiveInteger(update.sequenceNo)
      || !EXECUTION_STATUSES.has(update.status) || !nullableBoundedText(update.blockerReason, 2000)
      || !nullableCalendarDate(update.actualStart) || !nullableCalendarDate(update.actualFinish)
      || !nullableBoundedText(update.note, 4000) || !isIsoDate(update.updatedAt)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The execution update response violated its contract.', { uncertain: true });
    }
    return { update: { ...update }, replayed: value.replayed };
  }

  const MODEL_CAPABILITIES = new Set(['chat', 'structured_output', 'tools', 'vision', 'image_generation', 'image_edit', 'embeddings']);
  const MODEL_STATUSES = new Set(['enabled', 'disabled']);

  function validateModelProfileInput(value, updating = false) {
    if (!isPlainObject(value)) throw new ProjectApiError('INVALID_INPUT', 'A model profile is required.');
    const required = ['provider', 'displayName', 'protocol', 'endpoint', 'modelName', 'capabilities', 'contextWindow', 'region', 'dataRetention', 'credentialRef', 'status'];
    const keys = new Set(Object.keys(value));
    if (!updating && (keys.size !== required.length || required.some((key) => !keys.has(key)))) throw new ProjectApiError('INVALID_INPUT', 'The model profile fields are incomplete.');
    if (updating && (!keys.has('expectedVersion') || keys.size !== required.length + 1 || required.some((key) => !keys.has(key)))) throw new ProjectApiError('INVALID_INPUT', 'The model profile update fields are incomplete.');
    if (updating && !positiveInteger(value.expectedVersion)) throw new ProjectApiError('INVALID_INPUT', 'expectedVersion must be positive.');
    if ([value.provider, value.displayName, value.protocol, value.modelName, value.region, value.dataRetention, value.credentialRef].some((item) => !boundedText(item, 300))) throw new ProjectApiError('INVALID_INPUT', 'Model profile text is invalid.');
    if (value.protocol !== 'openai-compatible' || !/^env:[A-Z][A-Z0-9_]{2,127}$/.test(value.credentialRef)) throw new ProjectApiError('INVALID_INPUT', 'Only OpenAI-compatible profiles with env references are supported.');
    if (!/^https:\/\/|^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/.test(value.endpoint)) throw new ProjectApiError('INVALID_INPUT', 'Remote endpoints must use HTTPS and local HTTP must be loopback.');
    if (!Array.isArray(value.capabilities) || !value.capabilities.length || value.capabilities.some((item) => !MODEL_CAPABILITIES.has(item))) throw new ProjectApiError('INVALID_INPUT', 'Model capabilities are invalid.');
    if (value.contextWindow !== null && !positiveInteger(value.contextWindow)) throw new ProjectApiError('INVALID_INPUT', 'contextWindow is invalid.');
    if (!MODEL_STATUSES.has(value.status)) throw new ProjectApiError('INVALID_INPUT', 'Model status is invalid.');
    const body = { provider: value.provider.trim(), displayName: value.displayName.trim(), protocol: value.protocol, endpoint: value.endpoint.trim().replace(/\/$/, ''), modelName: value.modelName.trim(), capabilities: [...new Set(value.capabilities)].sort(), contextWindow: value.contextWindow, region: value.region.trim(), dataRetention: value.dataRetention.trim(), credentialRef: value.credentialRef.trim(), status: value.status };
    if (updating) body.expectedVersion = value.expectedVersion;
    return body;
  }

  function validateModelProfile(value) {
    const required = ['profileId', 'provider', 'displayName', 'protocol', 'endpoint', 'modelName', 'capabilities', 'contextWindow', 'region', 'dataRetention', 'credentialRef', 'credentialConfigured', 'status', 'health', 'version', 'createdAt', 'updatedAt', 'lastError'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !UUID_PATTERN.test(value.profileId || '')
      || !boundedText(value.provider, 100) || !boundedText(value.displayName, 120) || value.protocol !== 'openai-compatible'
      || !nonEmptyText(value.endpoint) || !nonEmptyText(value.modelName) || !Array.isArray(value.capabilities)
      || value.capabilities.some((item) => !MODEL_CAPABILITIES.has(item)) || (value.contextWindow !== null && !positiveInteger(value.contextWindow))
      || !boundedText(value.region, 80) || !boundedText(value.dataRetention, 200) || !/^env:[A-Z][A-Z0-9_]{2,127}$/.test(value.credentialRef || '') || typeof value.credentialConfigured !== 'boolean'
      || !MODEL_STATUSES.has(value.status) || value.health !== 'unverified' || !positiveInteger(value.version)
      || !isIsoDate(value.createdAt) || !isIsoDate(value.updatedAt) || !nullableBoundedText(value.lastError, 1000)) {
      throw new ProjectApiError('MALFORMED_RESPONSE', 'The model profile violated its contract.', { uncertain: true });
    }
    return { ...value };
  }

  function validateWorkbenchBriefInput(value) {
    const fields = ['deidentified', 'productName', 'productType', 'targetMarket', 'audience', 'objective', 'timeframe', 'background', 'constraints'];
    if (!isPlainObject(value) || !hasExactKeys(value, fields) || value.deidentified !== true || !Array.isArray(value.constraints) || value.constraints.some((item) => typeof item !== 'string')) throw new ProjectApiError('INVALID_INPUT', 'The deidentified Brief is incomplete.');
    fields.slice(1, -1).forEach((field) => { if (typeof value[field] !== 'string' || value[field].length > 4000) throw new ProjectApiError('INVALID_INPUT', `Brief field ${field} is invalid.`); });
    return { ...value, constraints: value.constraints.map((item) => item.trim()).filter(Boolean).slice(0, 20) };
  }

  function validateWorkbenchBrief(value) {
    const fields = ['briefId', 'createdAt', 'version', 'deidentified', 'productName', 'productType', 'targetMarket', 'audience', 'objective', 'timeframe', 'background', 'constraints', 'missingQuestions', 'status'];
    if (!isPlainObject(value) || !hasExactKeys(value, fields) || !UUID_PATTERN.test(value.briefId || '') || !isIsoDate(value.createdAt) || !positiveInteger(value.version) || value.deidentified !== true || !Array.isArray(value.constraints) || !Array.isArray(value.missingQuestions) || !['needs_clarification', 'ready'].includes(value.status)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Brief violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateResearchRunInput(value) {
    if (!isPlainObject(value) || !UUID_PATTERN.test(value.briefId || '') || !Array.isArray(value.sources) || !value.sources.length || !Array.isArray(value.observations) || !value.observations.length) throw new ProjectApiError('INVALID_INPUT', 'The research inputs are incomplete.');
    return { briefId: value.briefId, sources: value.sources, observations: value.observations };
  }

  function validateResearchRun(value) {
    if (!isPlainObject(value) || !hasExactKeys(value, ['runId', 'briefId', 'createdAt', 'status', 'sourceCount', 'researchTask', 'sources', 'observations']) || !UUID_PATTERN.test(value.runId || '') || !UUID_PATTERN.test(value.briefId || '') || !isIsoDate(value.createdAt) || !['needs_review', 'completed', 'failed'].includes(value.status) || !Array.isArray(value.sources) || !Array.isArray(value.observations)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The research run violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateProposalDraft(value) {
    if (!isPlainObject(value) || !hasExactKeys(value, ['draftId', 'briefId', 'researchRunId', 'createdAt', 'version', 'status', 'decision', 'decisionHistory', 'sections']) || !UUID_PATTERN.test(value.draftId || '') || !UUID_PATTERN.test(value.briefId || '') || !UUID_PATTERN.test(value.researchRunId || '') || !isIsoDate(value.createdAt) || !positiveInteger(value.version) || !['needs_review', 'approved', 'needs_revision', 'rejected'].includes(value.status) || (value.decision !== null && !isPlainObject(value.decision)) || !Array.isArray(value.decisionHistory) || !isPlainObject(value.sections)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The proposal draft violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateGeoQuerySetInput(value) {
    if (!isPlainObject(value) || !boundedText(value.product, 200) || !boundedText(value.market, 120) || !boundedText(value.language, 80) || !Array.isArray(value.queries) || value.queries.length < 1 || value.queries.length > 20 || value.queries.some((item) => !boundedText(item, 400))) throw new ProjectApiError('INVALID_INPUT', 'The GEO query set is incomplete.');
    return { product: value.product.trim(), market: value.market.trim(), language: value.language.trim(), queries: value.queries.map((item) => item.trim()) };
  }

  function validateGeoQuerySet(value) {
    if (!isPlainObject(value) || !UUID_PATTERN.test(value.querySetId || '') || !isIsoDate(value.createdAt) || !positiveInteger(value.version) || !boundedText(value.product, 200) || !boundedText(value.market, 120) || !boundedText(value.language, 80) || !Array.isArray(value.queries) || !value.queries.length || value.queries.length > 20 || value.queries.some((item) => !isPlainObject(item) || !UUID_PATTERN.test(item.queryId || '') || !boundedText(item.text, 400))) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO query set violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateGeoSnapshotInput(value) {
    if (!isPlainObject(value) || !['Google Search', 'Bing Search', 'ChatGPT', '其他（人工记录）'].includes(value.platform) || !UUID_PATTERN.test(value.queryId || '') || !['mentioned', 'not_mentioned', 'unclear'].includes(value.visibility) || !boundedText(value.observation, 2000) || !/^\d{4}-\d{2}-\d{2}$/.test(value.observedAt || '') || (value.citation !== null && value.citation !== '' && !boundedText(value.citation, 1000))) throw new ProjectApiError('INVALID_INPUT', 'The GEO snapshot is incomplete.');
    return { ...value, citation: value.citation || null };
  }

  function validateGeoSnapshot(value) {
    if (!isPlainObject(value) || !UUID_PATTERN.test(value.snapshotId || '') || !UUID_PATTERN.test(value.querySetId || '') || !isIsoDate(value.createdAt) || !['Google Search', 'Bing Search', 'ChatGPT', '其他（人工记录）'].includes(value.platform) || !UUID_PATTERN.test(value.queryId || '') || !boundedText(value.queryText, 400) || !['mentioned', 'not_mentioned', 'unclear'].includes(value.visibility) || !boundedText(value.observation, 2000) || !/^\d{4}-\d{2}-\d{2}$/.test(value.observedAt || '') || (value.citation !== null && !boundedText(value.citation, 1000))) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO snapshot violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateGeoTask(value) {
    if (!isPlainObject(value) || !UUID_PATTERN.test(value.taskId || '') || !UUID_PATTERN.test(value.snapshotId || '') || !UUID_PATTERN.test(value.querySetId || '') || !isIsoDate(value.createdAt) || value.status !== 'needs_review' || !boundedText(value.title, 500) || !boundedText(value.platform, 80) || !/^\d{4}-\d{2}-\d{2}$/.test(value.observedAt || '')) throw new ProjectApiError('MALFORMED_RESPONSE', 'The GEO task violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateContentBriefInput(value) {
    const fields = ['topic', 'channel', 'format', 'audience'];
    if (!isPlainObject(value) || fields.some((key) => !Object.prototype.hasOwnProperty.call(value, key)) || Object.keys(value).some((key) => !fields.includes(key) && !['briefId', 'expectedVersion'].includes(key)) || !boundedText(value.topic, 200) || !boundedText(value.channel, 100) || !boundedText(value.format, 100) || typeof value.audience !== 'string' || value.audience.length > 2000) throw new ProjectApiError('INVALID_INPUT', 'The content Brief is incomplete.');
    if (value.briefId !== undefined && !UUID_PATTERN.test(value.briefId)) throw new ProjectApiError('INVALID_INPUT', 'The Brief id is invalid.');
    if (value.expectedVersion !== undefined && !positiveInteger(value.expectedVersion)) throw new ProjectApiError('INVALID_INPUT', 'The Brief version is invalid.');
    return { topic: value.topic.trim(), channel: value.channel.trim(), format: value.format.trim(), audience: value.audience.trim(), ...(value.briefId ? { briefId: value.briefId, expectedVersion: value.expectedVersion } : {}) };
  }

  function validateContentBrief(value) {
    const required = ['briefId', 'createdAt', 'updatedAt', 'version', 'topic', 'channel', 'format', 'audience', 'status', 'approvedAt'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !UUID_PATTERN.test(value.briefId || '') || !isIsoDate(value.createdAt) || !isIsoDate(value.updatedAt) || !positiveInteger(value.version) || !boundedText(value.topic, 200) || !boundedText(value.channel, 100) || !boundedText(value.format, 100) || typeof value.audience !== 'string' || value.audience.length > 2000 || !['draft', 'approved'].includes(value.status) || (value.approvedAt !== undefined && value.approvedAt !== null && !isIsoDate(value.approvedAt))) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content Brief violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateContentAsset(value) {
    const required = ['assetId', 'briefId', 'createdAt', 'updatedAt', 'version', 'title', 'channel', 'format', 'assetType', 'prompt', 'status'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !UUID_PATTERN.test(value.assetId || '') || !UUID_PATTERN.test(value.briefId || '') || !isIsoDate(value.createdAt) || !isIsoDate(value.updatedAt) || !positiveInteger(value.version) || !boundedText(value.title, 300) || !boundedText(value.channel, 100) || !boundedText(value.format, 100) || !['content', 'image'].includes(value.assetType) || typeof value.prompt !== 'string' || value.prompt.length > 2000 || !['draft', 'needs_authorization', 'queued', 'ready', 'failed'].includes(value.status)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The content asset violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateCalendarItem(value) {
    const required = ['itemId', 'createdAt', 'updatedAt', 'version', 'title', 'date', 'source', 'note', 'status'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !UUID_PATTERN.test(value.itemId || '') || !isIsoDate(value.createdAt) || !isIsoDate(value.updatedAt) || !positiveInteger(value.version) || !boundedText(value.title, 200) || !isCalendarDate(value.date) || !boundedText(value.source, 100) || typeof value.note !== 'string' || value.note.length > 1000 || !['draft', 'confirmed'].includes(value.status)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The calendar item violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateObsidianConnection(value) {
    const required = ['connectionId', 'vaultPath', 'relativePaths', 'status', 'createdAt', 'updatedAt'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !nonEmptyText(value.connectionId) || !boundedText(value.vaultPath, 500) || !Array.isArray(value.relativePaths) || value.relativePaths.length > 200 || value.relativePaths.some((item) => !boundedText(item, 500)) || value.status !== 'connected' || !isIsoDate(value.createdAt) || !isIsoDate(value.updatedAt)) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Obsidian connection violated its contract.', { uncertain: true });
    return { ...value };
  }

  function validateObsidianNote(value) {
    const required = ['relativePath', 'title', 'modifiedAt', 'sha256', 'sizeBytes'];
    if (!isPlainObject(value) || !hasExactKeys(value, required) || !boundedText(value.relativePath, 500) || !boundedText(value.title, 500) || !isIsoDate(value.modifiedAt) || !SHA256_PATTERN.test(value.sha256) || !Number.isInteger(value.sizeBytes) || value.sizeBytes < 0) throw new ProjectApiError('MALFORMED_RESPONSE', 'The Obsidian note violated its contract.', { uncertain: true });
    return { ...value };
  }

  function nullableBoundedText(value, maximum) {
    return value === undefined || value === null || (typeof value === 'string' && value.length <= maximum);
  }

  function nullableCalendarDate(value) {
    return value === undefined || value === null || isCalendarDate(value);
  }

  function nonEmptyText(value) {
    return typeof value === 'string' && value.trim() !== '';
  }

  function boundedText(value, maximum) {
    return nonEmptyText(value) && value.trim().length <= maximum;
  }

  function isIsoDate(value) {
    return nonEmptyText(value)
      && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
      && !Number.isNaN(Date.parse(value));
  }

  function isCalendarDate(value) {
    if (!nonEmptyText(value) || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  function positiveInteger(value) {
    return Number.isInteger(value) && value > 0;
  }

  function isPlainObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  }

  function hasExactKeys(value, expected) {
    const actual = Object.keys(value).sort();
    const required = [...expected].sort();
    return actual.length === required.length && actual.every((key, index) => key === required[index]);
  }

  const api = {
    CANONICAL_MEDIA_TYPES,
    IMPORT_ROUTE,
    PROJECTS_ROUTE,
    MODEL_PROFILES_ROUTE,
    MODEL_MATCH_ROUTE,
    WORKBENCH_BRIEFS_ROUTE,
    WORKBENCH_RESEARCH_RUNS_ROUTE,
    WORKBENCH_PROPOSAL_DRAFTS_ROUTE,
    GEO_QUERY_SETS_ROUTE,
    CONTENT_BRIEFS_ROUTE,
    CONTENT_ASSETS_ROUTE,
    CALENDAR_ITEMS_ROUTE,
    OBSIDIAN_CONNECTION_ROUTE,
    OBSIDIAN_NOTES_ROUTE,
    ProjectApiError,
    createIdempotencyKey,
    createProjectApiClient,
    createRetryKeyManager,
    importFingerprint,
    importThenLoad,
    isSupportedImportName,
    isSupportedProposalName,
    loadInitialProject,
    normalizeUpload,
    validateImportResult,
    validateProjectDetail,
    validateProjectList,
    validateReviewCreateResult,
    validateReviewDetail,
    validateReviewDecisionResult,
    validateReviewRunSummary,
    validateWorkbenchBriefInput,
    validateWorkbenchBrief,
    validateResearchRun,
    validateProposalDraft,
    validateGeoQuerySetInput,
    validateGeoQuerySet,
    validateGeoSnapshotInput,
    validateGeoSnapshot,
    validateGeoTask,
    validateContentBriefInput,
    validateContentBrief,
    validateContentAsset,
    validateCalendarItem,
    validateObsidianConnection,
    validateObsidianNote,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  globalScope.MarketOpsProjectImport = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
