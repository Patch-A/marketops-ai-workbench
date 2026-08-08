(function projectImportModule(globalScope) {
  'use strict';

  const STORAGE_KEY = 'marketops.projects.v1';
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const SUPPORTED_IMPORT_EXTENSIONS = new Set(['md', 'markdown', 'csv', 'docx']);
  const SUPPORTED_PROPOSAL_EXTENSIONS = new Set(['md', 'markdown', 'docx']);

  function requireText(value, label) {
    if (typeof value !== 'string' || value.trim() === '') {
      throw new TypeError(`${label} is required.`);
    }
    return value.trim();
  }

  function requireHash(value, label) {
    const hash = requireText(value, label).toLowerCase();
    if (!SHA256_PATTERN.test(hash)) throw new TypeError(`${label} must be a SHA-256 hash.`);
    return hash;
  }

  function requireIsoDate(value, label) {
    const date = requireText(value, label);
    if (Number.isNaN(Date.parse(date))) throw new TypeError(`${label} must be an ISO date.`);
    return date;
  }

  function isSupportedImportName(name) {
    if (typeof name !== 'string') return false;
    const extension = name.trim().toLowerCase().split('.').pop();
    return SUPPORTED_IMPORT_EXTENSIONS.has(extension);
  }

  function isSupportedProposalName(name) {
    if (typeof name !== 'string') return false;
    const extension = name.trim().toLowerCase().split('.').pop();
    return SUPPORTED_PROPOSAL_EXTENSIONS.has(extension);
  }

  function createArtifactMetadata(input) {
    if (!input || typeof input !== 'object') throw new TypeError('Artifact input is required.');
    if (!isSupportedImportName(input.name)) throw new TypeError('Unsupported import format.');
    const size = Number(input.size);
    if (!Number.isInteger(size) || size < 0) throw new TypeError('Artifact size must be a non-negative integer.');
    return {
      id: requireText(input.id, 'Artifact id'),
      name: requireText(input.name, 'Artifact name'),
      type: typeof input.type === 'string' ? input.type : '',
      size,
      sha256: requireHash(input.sha256, 'Artifact hash'),
      retained: true,
    };
  }

  async function retainFileRecord(file, artifactId, store, retainedAt = new Date().toISOString()) {
    if (!file || typeof file !== 'object') throw new TypeError('File is required.');
    if (!store?.put) throw new TypeError('A file store is required.');
    const id = requireText(artifactId, 'Artifact id');
    const name = requireText(file.name, 'File name');
    const size = Number(file.size);
    if (!Number.isInteger(size) || size < 0) throw new TypeError('File size must be a non-negative integer.');
    const record = {
      id,
      file,
      name,
      type: typeof file.type === 'string' ? file.type : '',
      size,
      retainedAt: requireIsoDate(retainedAt, 'File retention time'),
    };
    await store.put(record);
    return record;
  }

  async function verifyRetainedProjectFiles(project, store) {
    if (!project?.sourceFile?.id || !project?.approvedProposal?.id) throw new TypeError('Project file references are required.');
    if (!store?.get) throw new TypeError('A readable file store is required.');
    const sourceRecord = await store.get(project.sourceFile.id);
    const proposalRecord = await store.get(project.approvedProposal.id);
    if (!sourceRecord) throw new Error('Source file is missing from local storage.');
    if (!proposalRecord) throw new Error('Approved proposal file is missing from local storage.');
    if (sourceRecord.name !== project.sourceFile.name || sourceRecord.size !== project.sourceFile.size) {
      throw new Error('Source file metadata does not match the retained project record.');
    }
    if (proposalRecord.name !== project.approvedProposal.name) {
      throw new Error('Approved proposal metadata does not match the retained project record.');
    }
    if (await sha256Blob(sourceRecord.file) !== project.sourceFile.sha256) {
      throw new Error('Source file hash does not match the retained project record.');
    }
    if (await sha256Blob(proposalRecord.file) !== project.approvedProposal.sha256) {
      throw new Error('Approved proposal hash does not match the retained project record.');
    }
    return true;
  }

  function openIndexedDbFileStore(indexedDb = globalScope.indexedDB) {
    if (!indexedDb?.open) return Promise.reject(new Error('IndexedDB is unavailable.'));
    return new Promise((resolve, reject) => {
      const request = indexedDb.open('marketops-files-v1', 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains('files')) request.result.createObjectStore('files', { keyPath: 'id' });
      };
      request.onerror = () => reject(request.error || new Error('Unable to open local file storage.'));
      request.onsuccess = () => {
        const database = request.result;
        resolve({
          put(value) {
            return new Promise((putResolve, putReject) => {
              const transaction = database.transaction('files', 'readwrite');
              transaction.objectStore('files').put(value);
              transaction.oncomplete = () => putResolve(value);
              transaction.onerror = () => putReject(transaction.error || new Error('Unable to retain the selected file.'));
              transaction.onabort = () => putReject(transaction.error || new Error('File retention was cancelled.'));
            });
          },
          has(id) {
            return new Promise((hasResolve, hasReject) => {
              const transaction = database.transaction('files', 'readonly');
              const request = transaction.objectStore('files').getKey(id);
              request.onsuccess = () => hasResolve(request.result !== undefined);
              request.onerror = () => hasReject(request.error || new Error('Unable to inspect local file storage.'));
            });
          },
          get(id) {
            return new Promise((getResolve, getReject) => {
              const transaction = database.transaction('files', 'readonly');
              const request = transaction.objectStore('files').get(id);
              request.onsuccess = () => getResolve(request.result);
              request.onerror = () => getReject(request.error || new Error('Unable to inspect local file storage.'));
            });
          },
        });
      };
    });
  }

  async function sha256Blob(blob, cryptoApi = globalScope.crypto) {
    if (!blob?.arrayBuffer) throw new TypeError('A readable file or Blob is required.');
    if (!cryptoApi?.subtle?.digest) throw new Error('SHA-256 is unavailable in this browser context.');
    const digest = await cryptoApi.subtle.digest('SHA-256', await blob.arrayBuffer());
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  function createProjectRecord(input) {
    if (!input || typeof input !== 'object') throw new TypeError('Project input is required.');
    if (!input.sourceFile?.retained || !input.approvedProposal?.retained) {
      throw new TypeError('Source file and approved proposal must be retained before project creation.');
    }
    if (input.approvedProposal?.status !== 'approved') throw new TypeError('An approved proposal version is required.');
    if (!isSupportedProposalName(input.approvedProposal.name)) throw new TypeError('Unsupported approved proposal format.');

    const sourceSize = Number(input.sourceFile.size);
    const proposalVersion = Number(input.approvedProposal.version);
    if (!Number.isInteger(sourceSize) || sourceSize < 0) throw new TypeError('Source file size must be a non-negative integer.');
    if (!Number.isInteger(proposalVersion) || proposalVersion < 1) throw new TypeError('Approved proposal version must be a positive integer.');

    return {
      schemaVersion: 1,
      id: requireText(input.id, 'Project id'),
      name: requireText(input.name, 'Project name'),
      clientName: typeof input.clientName === 'string' ? input.clientName.trim() : '',
      status: 'planning',
      sourceFile: {
        id: requireText(input.sourceFile.id, 'Source file id'),
        name: requireText(input.sourceFile.name, 'Source file name'),
        type: typeof input.sourceFile.type === 'string' ? input.sourceFile.type : '',
        size: sourceSize,
        sha256: requireHash(input.sourceFile.sha256, 'Source file hash'),
        retained: true,
      },
      approvedProposal: {
        id: requireText(input.approvedProposal.id, 'Approved proposal id'),
        version: proposalVersion,
        name: requireText(input.approvedProposal.name, 'Approved proposal name'),
        sha256: requireHash(input.approvedProposal.sha256, 'Approved proposal hash'),
        status: 'approved',
        retained: true,
        approvedAt: requireIsoDate(input.approvedProposal.approvedAt, 'Approval time'),
      },
      createdAt: requireIsoDate(input.createdAt, 'Project creation time'),
    };
  }

  function validateProjectRecord(record) {
    createProjectRecord(record);
    return true;
  }

  function listProjectRecords(storage = globalScope.localStorage) {
    if (!storage?.getItem) throw new TypeError('A storage adapter is required.');
    const value = storage.getItem(STORAGE_KEY);
    if (!value) return [];
    const records = JSON.parse(value);
    if (!Array.isArray(records)) throw new TypeError('Stored project records must be an array.');
    records.forEach(validateProjectRecord);
    return records;
  }

  function saveProjectRecord(record, storage = globalScope.localStorage) {
    validateProjectRecord(record);
    if (!storage?.setItem) throw new TypeError('A writable storage adapter is required.');
    const records = listProjectRecords(storage);
    const index = records.findIndex((item) => item.id === record.id);
    if (index >= 0) records[index] = record;
    else records.push(record);
    storage.setItem(STORAGE_KEY, JSON.stringify(records));
    return record;
  }

  const api = {
    STORAGE_KEY,
    createArtifactMetadata,
    createProjectRecord,
    isSupportedImportName,
    isSupportedProposalName,
    listProjectRecords,
    openIndexedDbFileStore,
    retainFileRecord,
    saveProjectRecord,
    sha256Blob,
    verifyRetainedProjectFiles,
    validateProjectRecord,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  globalScope.MarketOpsProjectImport = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
