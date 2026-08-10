(function projectImportModule(globalScope) {
  'use strict';

  const IMPORT_ROUTE = '/v1/project-imports';
  const PROJECTS_ROUTE = '/v1/projects';
  const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const SOURCE_EXTENSIONS = new Set(['md', 'markdown', 'csv', 'docx']);
  const PROPOSAL_EXTENSIONS = new Set(['md', 'markdown', 'docx']);
  const PROJECT_STATUSES = new Set(['planning', 'active', 'archived']);
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
      ? [...baseKeys, 'proposalVersion', 'approvalStatus', 'approvedAt']
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
      if (!Number.isInteger(value.proposalVersion)
        || value.proposalVersion < 1
        || value.approvalStatus !== 'approved'
        || !isIsoDate(value.approvedAt)) {
        throw new ProjectApiError('MALFORMED_RESPONSE', 'The approved proposal violated its contract.');
      }
      artifact.proposalVersion = value.proposalVersion;
      artifact.approvalStatus = value.approvalStatus;
      artifact.approvedAt = value.approvedAt;
    }
    return artifact;
  }

  function nonEmptyText(value) {
    return typeof value === 'string' && value.trim() !== '';
  }

  function isIsoDate(value) {
    return nonEmptyText(value)
      && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
      && !Number.isNaN(Date.parse(value));
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
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  globalScope.MarketOpsProjectImport = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
