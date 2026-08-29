"""Transactional PostgreSQL adapter for immutable project learning snapshots."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, AsyncIterator, Callable, Mapping
from uuid import UUID

from .service import (CapsuleInputs, FeedbackSourceReference, KnowledgeEvidence, KnowledgeItem,
                      LearningFailure, LearningScopeContext, OutcomeRecord, ProjectCapsule,
                      RetrospectiveRecord, TaskExecutionSnapshot, TaskSnapshot, build_project_capsule)

_SET_SCOPE_SQL = "SELECT set_config('app.workspace_id',$1,true),set_config('app.client_id',$2,true),set_config('app.project_id',$3,true),set_config('app.actor_id',$4,true)"
_LOCK_SQL = "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1::text,0))"
_BRIEF_SQL = "SELECT p.approved_proposal_artifact_id artifact_id,p.approved_proposal_version_id version_id,p.approved_proposal_number proposal_version,v.sha256 FROM marketops.projects p JOIN marketops.artifact_versions v ON v.id=p.approved_proposal_version_id WHERE p.id=$1 AND p.organization_id=$2 AND p.workspace_id=$3 AND p.client_id=$4 AND p.created_by=$5"
_PLAN_SQL = """SELECT p.id plan_id,v.id plan_version_id,v.plan_version,v.plan_digest,a.id approval_id,a.plan_id approval_plan_id,a.plan_version_id approval_plan_version_id,a.plan_digest approval_plan_digest,a.schedule_snapshot_id approval_schedule_snapshot_id,a.schedule_digest approval_schedule_digest,s.id schedule_snapshot_id,s.plan_id schedule_plan_id,s.plan_version_id schedule_plan_version_id,s.plan_digest schedule_plan_digest,s.schedule_digest FROM marketops.wbs_plans p JOIN marketops.wbs_plan_versions v ON v.plan_id=p.id JOIN marketops.wbs_plan_approvals a ON a.plan_version_id=v.id JOIN marketops.schedule_snapshots s ON s.id=a.schedule_snapshot_id WHERE p.organization_id=$1 AND p.workspace_id=$2 AND p.client_id=$3 AND p.project_id=$4 AND p.created_by=$5 AND v.organization_id=$1 AND v.workspace_id=$2 AND v.client_id=$3 AND v.project_id=$4 AND a.organization_id=$1 AND a.workspace_id=$2 AND a.client_id=$3 AND a.project_id=$4 AND s.organization_id=$1 AND s.workspace_id=$2 AND s.client_id=$3 AND s.project_id=$4 AND a.plan_id=p.id AND s.plan_id=p.id AND s.plan_version_id=v.id AND a.plan_digest=v.plan_digest AND s.plan_digest=v.plan_digest AND a.schedule_digest=s.schedule_digest ORDER BY v.plan_version DESC LIMIT 1"""
_TASKS_SQL = "SELECT task_id,title,planned_start,planned_finish,duration_workdays FROM marketops.wbs_tasks WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND plan_version_id=$5 AND created_by=$6 ORDER BY ordinal"
_EXECUTIONS_SQL = "SELECT DISTINCT ON (task_id) id,task_id,status,blocker_reason,actual_start,actual_finish,sequence_no,updated_by,updated_at FROM marketops.wbs_task_execution_updates WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND plan_version_id=$5 AND updated_by=$6 ORDER BY task_id,sequence_no DESC"
_ARTIFACT_SOURCE_SQL = "SELECT sha256 FROM marketops.artifact_versions WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND created_by=$6"
_EXECUTION_SOURCE_SQL = "SELECT id,task_id,status,blocker_reason,actual_start,actual_finish,sequence_no,updated_by,updated_at FROM marketops.wbs_task_execution_updates WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND plan_version_id=$6 AND updated_by=$7"
_SCHEDULE_SOURCE_SQL = "SELECT schedule_digest FROM marketops.schedule_snapshots WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND created_by=$6"
_CAPSULE_SQL = "SELECT id,capsule_version,status,capsule_digest,payload,outcome_count,retrospective_count,knowledge_count,created_at FROM marketops.project_capsules WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND created_by=$5 AND capsule_digest=decode($6,'hex')"
_CAPSULE_BY_ID_SQL = "SELECT id,capsule_version,status,capsule_digest,payload,outcome_count,retrospective_count,knowledge_count,created_at FROM marketops.project_capsules WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND created_by=$6"
_LIST_CAPSULES_SQL = "SELECT id,capsule_version,status,capsule_digest,outcome_count,retrospective_count,knowledge_count,created_at FROM marketops.project_capsules WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND created_by=$5 ORDER BY capsule_version DESC"
_KNOWLEDGE_BY_CAPSULE_SQL = "SELECT id,capsule_id,ordinal,scope,type,status,classification,content,content_sha256,confidence FROM marketops.knowledge_items WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND created_by=$5 AND capsule_id=$6 ORDER BY ordinal"
_LIST_KNOWLEDGE_SQL = "SELECT id,capsule_id,ordinal,scope,type,status,classification,content,content_sha256,confidence FROM marketops.knowledge_items WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND created_by=$5 ORDER BY capsule_id,ordinal"
_KNOWLEDGE_BY_ID_SQL = "SELECT id,capsule_id,ordinal,scope,type,status,classification,content,content_sha256,confidence FROM marketops.knowledge_items WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND created_by=$6"
_KNOWLEDGE_EVIDENCE_SQL = "SELECT source_type,source_id,binding_sha256 FROM marketops.knowledge_item_evidence WHERE knowledge_id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND created_by=$6 ORDER BY ordinal"
_KNOWLEDGE_VERSIONS_SQL = "SELECT version,content,content_sha256,created_at FROM marketops.knowledge_item_versions WHERE knowledge_id=$1 AND organization_id=$2 AND workspace_id=$3 AND client_id=$4 AND project_id=$5 AND created_by=$6 ORDER BY version"
_NEXT_SQL = "SELECT COALESCE(MAX(capsule_version),0)+1 FROM marketops.project_capsules WHERE organization_id=$1 AND workspace_id=$2 AND client_id=$3 AND project_id=$4 AND created_by=$5"
_INSERT_CAPSULE_SQL = """INSERT INTO marketops.project_capsules(id,organization_id,workspace_id,client_id,project_id,proposal_artifact_id,proposal_version_id,proposal_version,proposal_sha256,plan_id,plan_version_id,plan_version,plan_digest,approval_id,schedule_snapshot_id,schedule_digest,capsule_version,status,capsule_digest,payload,outcome_count,retrospective_count,knowledge_count,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,decode($9,'hex'),$10,$11,$12,decode($13,'hex'),$14,$15,decode($16,'hex'),$17,'ready',decode($18,'hex'),$19::jsonb,$20,$21,$22,$23,$24)"""
_INSERT_AUDIT_SQL = "INSERT INTO marketops.audit_events(id,organization_id,workspace_id,client_id,project_id,actor_id,action,target_type,target_id,event_data,created_at) VALUES($1,$2,$3,$4,$5,$6,'project_capsule.finalized','project_capsule',$7,$8::jsonb,$9)"
_INSERT_OUTCOME_SQL = "INSERT INTO marketops.project_outcomes(id,organization_id,workspace_id,client_id,project_id,capsule_id,metric,planned_value,actual_value,unit,classification,source_type,source_id,source_binding_sha256,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'outcome_observation',$11,$12,decode($13,'hex'),$14,$15)"
_INSERT_RETRO_SQL = "INSERT INTO marketops.project_retrospectives(id,organization_id,workspace_id,client_id,project_id,capsule_id,finding,classification,reusable_candidate,evidence,evidence_sha256,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,decode($11,'hex'),$12,$13)"
_INSERT_KNOWLEDGE_SQL = "INSERT INTO marketops.knowledge_items(id,organization_id,workspace_id,client_id,project_id,capsule_id,ordinal,scope,type,status,classification,content,content_sha256,confidence,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,'project',$8,'candidate',$9,$10,decode($11,'hex'),1.0,$12,$13)"
_INSERT_KNOWLEDGE_VERSION_SQL = "INSERT INTO marketops.knowledge_item_versions(knowledge_id,version,organization_id,workspace_id,client_id,project_id,capsule_id,content,content_sha256,evidence_digest,created_by,created_at) VALUES($1,1,$2,$3,$4,$5,$6,$7,decode($8,'hex'),decode($9,'hex'),$10,$11)"
_INSERT_EVIDENCE_SQL = "INSERT INTO marketops.knowledge_item_evidence(knowledge_id,ordinal,organization_id,workspace_id,client_id,project_id,source_type,source_id,binding_sha256,created_by,created_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,decode($9,'hex'),$10,$11)"

@dataclass(frozen=True)
class CapsuleReadModel:
    capsule_id: str; capsule_version: int; status: str; capsule_digest: str; payload: Mapping[str, Any]; outcome_count: int; retrospective_count: int; knowledge_count: int; created_at: datetime; knowledge_items: tuple[KnowledgeItem,...]
@dataclass(frozen=True)
class CapsuleSummary:
    capsule_id: str; capsule_version: int; status: str; capsule_digest: str; outcome_count: int; retrospective_count: int; knowledge_count: int; created_at: datetime
@dataclass(frozen=True)
class PersistCapsuleResult:
    capsule: CapsuleReadModel; replayed: bool
@dataclass(frozen=True)
class KnowledgeVersionView:
    version: int; content: str; content_sha256: str; created_at: datetime
@dataclass(frozen=True)
class KnowledgeReadModel:
    item: KnowledgeItem; versions: tuple[KnowledgeVersionView,...]; evidence: tuple[KnowledgeEvidence,...]

class AsyncpgLearningRepository:
    def __init__(self,pool:Any,*,id_factory:Callable[[],str],clock:Callable[[],datetime]): self.pool,self.id_factory,self.clock=pool,id_factory,clock
    async def finalize_capsule(self,scope:LearningScopeContext,project_id:str,outcomes:tuple[OutcomeRecord,...],retrospectives:tuple[RetrospectiveRecord,...])->PersistCapsuleResult:
        now=self.clock()
        try:
            async with self._transaction(scope,project_id) as c:
                await c.fetchval(_LOCK_SQL,project_id)
                inputs=await self._inputs(c,scope,project_id,outcomes,retrospectives)
                capsule=build_project_capsule(inputs,scope)
                old=await c.fetchrow(_CAPSULE_SQL,*self._scope(scope,project_id),capsule.capsule_digest)
                if old: return PersistCapsuleResult(await self._load_capsule(c,scope,project_id,old),True)
                version=int(await c.fetchval(_NEXT_SQL,*self._scope(scope,project_id)))
                s=self._scope(scope,project_id)
                await c.execute(_INSERT_CAPSULE_SQL,capsule.capsule_id,*s[:4],inputs.proposal_artifact_id,inputs.proposal_version_id,inputs.proposal_version,inputs.proposal_sha256,inputs.plan_id,inputs.plan_version_id,inputs.plan_version,inputs.plan_digest,inputs.approval_id,inputs.schedule_snapshot_id,inputs.schedule_digest,version,capsule.capsule_digest,_json(capsule.payload),len(outcomes),len(retrospectives),len(capsule.knowledge_items),scope.actor_id,now)
                for outcome in inputs.outcomes:
                    await c.execute(_INSERT_OUTCOME_SQL,outcome.outcome_id,*s[:4],capsule.capsule_id,outcome.metric,outcome.planned_value,outcome.actual_value,outcome.unit,outcome.source.source_type,outcome.source.source_id,outcome.source.binding_sha256,scope.actor_id,now)
                for retro in inputs.retrospectives:
                    evidence=[_source_dict(value) for value in _sorted_feedback_sources(retro.evidence)]
                    await c.execute(_INSERT_RETRO_SQL,retro.retrospective_id,*s[:4],capsule.capsule_id,retro.finding,retro.classification,retro.reusable_candidate,_json(evidence),_sha(evidence),scope.actor_id,now)
                for item in capsule.knowledge_items:
                    await c.execute(_INSERT_KNOWLEDGE_SQL,item.knowledge_id,*s[:4],capsule.capsule_id,item.ordinal,item.type,item.classification,item.content,item.content_sha256,scope.actor_id,now)
                    evidence_digest=_sha([_evidence_dict(value) for value in item.evidence])
                    await c.execute(_INSERT_KNOWLEDGE_VERSION_SQL,item.knowledge_id,*s[:4],capsule.capsule_id,item.content,item.content_sha256,evidence_digest,scope.actor_id,now)
                    for ordinal,evidence in enumerate(item.evidence,1):
                        await c.execute(_INSERT_EVIDENCE_SQL,item.knowledge_id,ordinal,*s[:4],evidence.source_type,evidence.source_id,evidence.binding_sha256,scope.actor_id,now)
                await c.execute(_INSERT_AUDIT_SQL,_uuid(self.id_factory()),*s[:4],scope.actor_id,capsule.capsule_id,_json({'capsuleDigest':capsule.capsule_digest,'outcomeCount':len(outcomes),'retrospectiveCount':len(retrospectives),'knowledgeCount':len(capsule.knowledge_items)}),now)
                return PersistCapsuleResult(CapsuleReadModel(capsule.capsule_id,version,capsule.status,capsule.capsule_digest,capsule.payload,len(outcomes),len(retrospectives),len(capsule.knowledge_items),now,capsule.knowledge_items),False)
        except LearningFailure: raise
        except asyncio.CancelledError: raise
        except Exception: raise LearningFailure('LEARNING_WRITE_FAILED','project capsule could not be persisted') from None
    async def read_capsule(self,scope:LearningScopeContext,project_id:str,capsule_id:str)->CapsuleReadModel|None:
        try:
            async with self._read_connection(scope,project_id) as c:
                row=await c.fetchrow(_CAPSULE_BY_ID_SQL,capsule_id,*self._scope(scope,project_id))
                return None if row is None else await self._load_capsule(c,scope,project_id,row)
        except asyncio.CancelledError: raise
        except Exception: raise LearningFailure('LEARNING_READ_FAILED','project capsule could not be read') from None
    async def list_capsules(self,scope:LearningScopeContext,project_id:str)->tuple[CapsuleSummary,...]:
        try:
            async with self._read_connection(scope,project_id) as c:
                rows=await c.fetch(_LIST_CAPSULES_SQL,*self._scope(scope,project_id))
                return tuple(CapsuleSummary(_uuid(r['id']),int(r['capsule_version']),str(r['status']),_hex(r['capsule_digest']),int(r['outcome_count']),int(r['retrospective_count']),int(r['knowledge_count']),r['created_at']) for r in rows)
        except asyncio.CancelledError: raise
        except Exception: raise LearningFailure('LEARNING_READ_FAILED','project capsules could not be read') from None
    async def list_knowledge(self,scope:LearningScopeContext,project_id:str)->tuple[KnowledgeItem,...]:
        try:
            async with self._read_connection(scope,project_id) as c:
                rows=await c.fetch(_LIST_KNOWLEDGE_SQL,*self._scope(scope,project_id))
                return tuple([await self._load_knowledge(c,scope,project_id,row) for row in rows])
        except asyncio.CancelledError: raise
        except Exception: raise LearningFailure('LEARNING_READ_FAILED','candidate knowledge could not be read') from None
    async def read_knowledge(self,scope:LearningScopeContext,project_id:str,knowledge_id:str)->KnowledgeReadModel|None:
        try:
            async with self._read_connection(scope,project_id) as c:
                row=await c.fetchrow(_KNOWLEDGE_BY_ID_SQL,knowledge_id,*self._scope(scope,project_id))
                if row is None: return None
                item=await self._load_knowledge(c,scope,project_id,row)
                versions=await c.fetch(_KNOWLEDGE_VERSIONS_SQL,knowledge_id,*self._scope(scope,project_id))
                views=tuple(KnowledgeVersionView(int(value['version']),str(value['content']),_hex(value['content_sha256']),value['created_at']) for value in versions)
                return KnowledgeReadModel(item,views,item.evidence)
        except asyncio.CancelledError: raise
        except Exception: raise LearningFailure('LEARNING_READ_FAILED','candidate knowledge could not be read') from None
    async def _load_capsule(self,c:Any,scope:LearningScopeContext,project_id:str,row:Any)->CapsuleReadModel:
        rows=await c.fetch(_KNOWLEDGE_BY_CAPSULE_SQL,*self._scope(scope,project_id),_uuid(row['id']))
        items=tuple([await self._load_knowledge(c,scope,project_id,value) for value in rows])
        return _model(row,items)
    async def _load_knowledge(self,c:Any,scope:LearningScopeContext,project_id:str,row:Any)->KnowledgeItem:
        rows=await c.fetch(_KNOWLEDGE_EVIDENCE_SQL,_uuid(row['id']),*self._scope(scope,project_id))
        evidence=tuple(KnowledgeEvidence(str(value['source_type']),str(value['source_id']),project_id,_hex(value['binding_sha256'])) for value in rows)
        return KnowledgeItem(_uuid(row['id']),int(row['ordinal']),str(row['type']),str(row['status']),str(row['classification']),str(row['content']),float(row['confidence']),evidence,_hex(row['content_sha256']),str(row['scope']),project_id)
    async def _inputs(self,c:Any,scope:LearningScopeContext,project_id:str,outcomes:tuple[OutcomeRecord,...],retrospectives:tuple[RetrospectiveRecord,...])->CapsuleInputs:
        s=self._scope(scope,project_id); brief=await c.fetchrow(_BRIEF_SQL,project_id,*s[:3],scope.actor_id); plan=await c.fetchrow(_PLAN_SQL,*s)
        if brief is None: raise LearningFailure('CAPSULE_NOT_FOUND','project or approved proposal is unavailable')
        if plan is None: raise LearningFailure('CAPSULE_NOT_READY','project has no approved WBS plan')
        pid=_uuid(plan['plan_version_id']); tasks=tuple(TaskSnapshot(str(r['task_id']),str(r['title']),_date(r['planned_start']),_date(r['planned_finish']),int(r['duration_workdays'])) for r in await c.fetch(_TASKS_SQL,*s[:4],pid,scope.actor_id))
        executions=tuple(_execution(r) for r in await c.fetch(_EXECUTIONS_SQL,*s[:4],pid,scope.actor_id))
        schedule_id=_uuid(plan['schedule_snapshot_id'])
        resolved_outcomes=[]
        for item in outcomes:
            resolved_outcomes.append(replace(item,source=await self._resolve_source(c,scope,project_id,pid,schedule_id,item.source)))
        resolved_retrospectives=[]
        for item in retrospectives:
            evidence=[]
            for source in item.evidence:
                evidence.append(await self._resolve_source(c,scope,project_id,pid,schedule_id,source))
            resolved_retrospectives.append(replace(item,evidence=tuple(evidence)))
        return CapsuleInputs(project_id,_uuid(brief['artifact_id']),_uuid(brief['version_id']),int(brief['proposal_version']),_hex(brief['sha256']),_uuid(plan['plan_id']),pid,int(plan['plan_version']),_hex(plan['plan_digest']),_uuid(plan['approval_id']),_uuid(plan['approval_plan_id']),_uuid(plan['approval_plan_version_id']),_hex(plan['approval_plan_digest']),_uuid(plan['approval_schedule_snapshot_id']),_hex(plan['approval_schedule_digest']),schedule_id,_uuid(plan['schedule_plan_id']),_uuid(plan['schedule_plan_version_id']),_hex(plan['schedule_plan_digest']),_hex(plan['schedule_digest']),tasks,executions,tuple(resolved_outcomes),tuple(resolved_retrospectives))
    async def _resolve_source(self,c:Any,scope:LearningScopeContext,project_id:str,plan_version_id:str,schedule_id:str,source:FeedbackSourceReference)->FeedbackSourceReference:
        scope_values=self._scope(scope,project_id)
        if source.source_type == 'artifact_version':
            row=await c.fetchrow(_ARTIFACT_SOURCE_SQL,source.source_id,*scope_values)
            binding=None if row is None else _hex(row['sha256'])
        elif source.source_type == 'task_execution':
            row=await c.fetchrow(_EXECUTION_SOURCE_SQL,source.source_id,*scope_values[:4],plan_version_id,scope.actor_id)
            binding=None if row is None else _execution(row).execution_digest
        elif source.source_type == 'schedule_snapshot' and source.source_id == schedule_id:
            row=await c.fetchrow(_SCHEDULE_SOURCE_SQL,source.source_id,*scope_values)
            binding=None if row is None else _hex(row['schedule_digest'])
        else:
            row=None; binding=None
        if binding is None: raise LearningFailure('CAPSULE_NOT_READY','feedback source is unavailable')
        return FeedbackSourceReference(source.source_type,source.source_id,project_id,binding)
    @asynccontextmanager
    async def _transaction(self,scope:LearningScopeContext,project_id:str)->AsyncIterator[Any]:
        async with self.pool.acquire() as c:
            async with c.transaction():
                await c.execute(_SET_SCOPE_SQL,scope.workspace_id,scope.client_id,project_id,scope.actor_id); yield c
    @asynccontextmanager
    async def _read_connection(self,scope:LearningScopeContext,project_id:str)->AsyncIterator[Any]:
        async with self.pool.acquire() as c:
            async with c.transaction(readonly=True):
                await c.execute(_SET_SCOPE_SQL,scope.workspace_id,scope.client_id,project_id,scope.actor_id); yield c
    @staticmethod
    def _scope(scope:LearningScopeContext,project_id:str)->tuple[str,str,str,str,str]: return scope.organization_id,scope.workspace_id,scope.client_id,project_id,scope.actor_id

def _execution(row:Any)->TaskExecutionSnapshot:
    digest=_sha({'id':_uuid(row['id']),'taskId':str(row['task_id']),'status':str(row['status']),'blockerReason':row['blocker_reason'],'actualStart':_date(row['actual_start']),'actualFinish':_date(row['actual_finish']),'sequence':int(row['sequence_no']),'updatedBy':_uuid(row['updated_by']),'updatedAt':row['updated_at'].isoformat()})
    return TaskExecutionSnapshot(_uuid(row['id']),str(row['task_id']),str(row['status']),_date(row['actual_start']),_date(row['actual_finish']),None if row['blocker_reason'] is None else str(row['blocker_reason']),digest)
def _model(row:Any,items:tuple[KnowledgeItem,...]=())->CapsuleReadModel: return CapsuleReadModel(_uuid(row['id']),int(row['capsule_version']),str(row['status']),_hex(row['capsule_digest']),row['payload'] if isinstance(row['payload'],Mapping) else json.loads(str(row['payload'])),int(row['outcome_count']),int(row['retrospective_count']),int(row['knowledge_count']),row['created_at'],items)
def _uuid(v:Any)->str: return str(UUID(str(v)))
def _hex(v:Any)->str: return bytes(v).hex()
def _date(v:Any)->str|None: return None if v is None else v.isoformat() if isinstance(v,date) else str(v)
def _json(v:Any)->str: return json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(',',':'))
def _sha(v:Any)->str: return hashlib.sha256(_json(v).encode()).hexdigest()
def _source_dict(value:FeedbackSourceReference)->dict[str,str]: return {'sourceType':value.source_type,'sourceId':value.source_id,'projectId':value.project_id,'bindingSha256':value.binding_sha256}
def _evidence_dict(value:KnowledgeEvidence)->dict[str,str]: return {'sourceType':value.source_type,'sourceId':value.source_id,'projectId':value.project_id,'bindingSha256':value.binding_sha256}
def _sorted_feedback_sources(values:tuple[FeedbackSourceReference,...])->tuple[FeedbackSourceReference,...]: return tuple(sorted(values,key=lambda value:(value.source_type,value.source_id,value.binding_sha256)))
