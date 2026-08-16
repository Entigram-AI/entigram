import json
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from .paths import CANONICAL_LEDGER_NAME, LEGACY_LEDGER_NAME
from entigram.governance.grounding import (
    LIFECYCLE_PROPOSED,
    LIFECYCLE_VERIFIED,
)

DEFAULT_SQLITE_TIMEOUT_SEC = 10.0
DEFAULT_BUSY_TIMEOUT_MS = 10000
TASK_RISK_REQUIRED_SCORE = {
    "read_only": 0.10,
    "low_risk": 0.35,
    "medium_risk": 0.60,
    "high_risk": 0.80,
    "critical": 0.95,
}


class ActionAttestationReplayError(ValueError):
    """Raised when an admitted shared-trust workload assertion was already consumed."""


class LedgerManager:
    def __init__(self, db_path: str):
        self.db_path = self._normalize_db_path(db_path)
        # Keep a persistent connection for in-memory databases to avoid losing data
        self._memory_conn = None
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(
                self.db_path,
                timeout=DEFAULT_SQLITE_TIMEOUT_SEC,
                check_same_thread=False,
            )
            self._configure_connection(self._memory_conn)
            
        self._ensure_db()

    def _normalize_db_path(self, db_path: str) -> str:
        if db_path == ":memory:":
            return db_path

        path = Path(db_path).expanduser()
        if (
            path.name == LEGACY_LEDGER_NAME
            and not path.exists()
            and (path.parent / CANONICAL_LEDGER_NAME).exists()
        ):
            return str(path.parent / CANONICAL_LEDGER_NAME)
        return str(path)

    def _get_connection(self):
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self.db_path, timeout=DEFAULT_SQLITE_TIMEOUT_SEC)
        self._configure_connection(conn)
        return conn

    def _configure_connection(self, conn):
        conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};")
        conn.execute("PRAGMA foreign_keys=ON;")
        if self.db_path != ":memory:":
            mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
            if not mode or str(mode[0]).lower() != "wal":
                raise sqlite3.OperationalError(
                    f"Unable to enable WAL mode for SQLite ledger {self.db_path}"
                )

    def close(self):
        if self._memory_conn is not None:
            self._memory_conn.close()
            self._memory_conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _ensure_db(self):
        """Creates the human_resolutions and conflicts tables if they don't exist."""
        if self.db_path != ":memory:":
            db_dir = Path(self.db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)
        
        conn = self._get_connection()
        with conn:
            # Table for settled decisions (Immutable Append-only)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS human_resolutions (
                    id INTEGER PRIMARY KEY,
                    conflict_id TEXT,
                    entity_type TEXT,
                    resolved_state TEXT,
                    rationale TEXT,
                    version INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Table for pending contradictions
            conn.execute('''
                CREATE TABLE IF NOT EXISTS conflicts (
                    id INTEGER PRIMARY KEY,
                    conflict_id TEXT UNIQUE,
                    entity_type TEXT,
                    proposed_states TEXT, -- JSON string of competing states
                    source_agents TEXT,   -- JSON string of agent IDs
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Table for Semantic Alignments (Cross-domain Governance)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS semantic_alignments (
                    id INTEGER PRIMARY KEY,
                    source_domain TEXT,
                    target_domain TEXT,
                    source_concept TEXT,
                    target_concept TEXT,
                    relation TEXT DEFAULT 'skos:exactMatch',
                    confidence REAL,
                    rationale TEXT,
                    status TEXT DEFAULT 'pending',
                    lifecycle_status TEXT DEFAULT 'proposed',
                    evidence_type TEXT,
                    source_artifact TEXT,
                    verified INTEGER DEFAULT 0,
                    verified_by TEXT,
                    verified_at DATETIME,
                    semantic_confidence REAL,
                    schema_confidence REAL,
                    data_confidence REAL,
                    human_review_confidence REAL,
                    runtime_observation_confidence REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_domain, target_domain, source_concept, target_concept)
                )
            ''')
            self._ensure_columns(conn, "semantic_alignments", {
                "lifecycle_status": "TEXT DEFAULT 'proposed'",
                "evidence_type": "TEXT",
                "source_artifact": "TEXT",
                "verified": "INTEGER DEFAULT 0",
                "verified_by": "TEXT",
                "verified_at": "DATETIME",
                "semantic_confidence": "REAL",
                "schema_confidence": "REAL",
                "data_confidence": "REAL",
                "human_review_confidence": "REAL",
                "runtime_observation_confidence": "REAL",
            })
            # Table for Global Synonym Mapping (Phase 3: Macro Reconciliation)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS synonyms (
                    id INTEGER PRIMARY KEY,
                    term TEXT,
                    synonym TEXT,
                    confidence REAL DEFAULT 1.0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(term, synonym)
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_synonyms_term
                ON synonyms(term)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_alignments_src_domain
                ON semantic_alignments(source_domain)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_alignments_concepts
                ON semantic_alignments(source_concept, target_concept)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_alignments_trust
                ON semantic_alignments(lifecycle_status, verified, evidence_type, confidence)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_resolutions_conflict
                ON human_resolutions(conflict_id, version)
            ''')
            # Table for durable delivery evidence (commissioner passes, command runs, artifact reviews)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS delivery_evidence (
                    id INTEGER PRIMARY KEY,
                    evidence_type TEXT NOT NULL,
                    expectation_name TEXT,
                    artifact_ref TEXT NOT NULL,
                    command TEXT,
                    result_summary TEXT,
                    passed INTEGER DEFAULT 1,
                    agent_id TEXT,
                    observed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._ensure_columns(conn, "delivery_evidence", {
                "expectation_name": "TEXT",
                "agent_id": "TEXT",
            })
            # Table for agent improvement proposals
            conn.execute('''
                CREATE TABLE IF NOT EXISTS improvement_proposals (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    affected_model TEXT NOT NULL,
                    proposed_change TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    expected_benefit TEXT,
                    lifecycle_status TEXT DEFAULT 'Proposed',
                    created_by TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._ensure_columns(conn, "improvement_proposals", {
                "lifecycle_status": "TEXT DEFAULT 'Proposed'",
                "created_by": "TEXT",
            })
            # Table for reusable lessons (Entigram_Lesson operational path)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY,
                    source_task TEXT,
                    lesson TEXT NOT NULL,
                    reusable_rule TEXT,
                    confidence REAL DEFAULT 1.0,
                    lifecycle_status TEXT DEFAULT 'Active',
                    agent_id TEXT,
                    observed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._ensure_columns(conn, "lessons", {
                "agent_id": "TEXT",
            })
            # Table for agent capability routing across mixed agent classes.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_registry (
                    id INTEGER PRIMARY KEY,
                    agent_id TEXT UNIQUE NOT NULL,
                    agent_class TEXT,
                    provider TEXT,
                    model TEXT,
                    reliability_score REAL DEFAULT 0.5,
                    capability_scores TEXT DEFAULT '{}',
                    allowed_task_classes TEXT DEFAULT '[]',
                    restricted_task_classes TEXT DEFAULT '[]',
                    last_workspace_seen TEXT,
                    failure_history TEXT DEFAULT '[]',
                    successful_handoffs INTEGER DEFAULT 0,
                    notes TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Table for durable task assignment decisions.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    id INTEGER PRIMARY KEY,
                    task_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    required_score REAL NOT NULL,
                    details TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'Queued',
                    assigned_agent_id TEXT,
                    assignment_rationale TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Table for token-window checkpointing and external scheduler resume.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS agent_hibernations (
                    id INTEGER PRIMARY KEY,
                    hibernate_id TEXT UNIQUE NOT NULL,
                    agent_id TEXT NOT NULL,
                    run_id TEXT,
                    status TEXT NOT NULL,
                    token_threshold INTEGER,
                    remaining_tokens INTEGER,
                    refresh_window_end TEXT,
                    resume_after TEXT,
                    checkpoint_summary TEXT,
                    next_action TEXT,
                    pending_task_ids TEXT DEFAULT '[]',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    resumed_at DATETIME
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_agent_registry_score
                ON agent_registry(reliability_score, updated_at)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_status
                ON agent_tasks(status, risk_level, required_score)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_agent_hibernations_resume
                ON agent_hibernations(status, resume_after)
            ''')
            # Aggregate-only accounting for Entigram-owned context and tool traffic.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY,
                    operation TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    input_characters INTEGER NOT NULL DEFAULT 0,
                    output_characters INTEGER NOT NULL DEFAULT 0,
                    estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_output_tokens INTEGER NOT NULL DEFAULT 0,
                    lifecycle_state TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT DEFAULT '{}',
                    observed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_usage_events_operation
                ON usage_events(operation, id)
            ''')
            # Table for delivery snapshots (frozen boot state at commission pass)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS delivery_snapshots (
                    id INTEGER PRIMARY KEY,
                    snapshot_id TEXT UNIQUE,
                    expectation_count INTEGER,
                    missing_proof_count INTEGER,
                    schema_hash TEXT,
                    agent_id TEXT,
                    warden_status TEXT,
                    evidence_ids TEXT,
                    artifact_ids TEXT,
                    metadata TEXT,
                    snapped_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            self._ensure_columns(conn, "delivery_snapshots", {
                "artifact_ids": "TEXT",
            })
            # Table for source-control-neutral local artifacts captured at delivery time
            conn.execute('''
                CREATE TABLE IF NOT EXISTS delivery_artifacts (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    artifact_role TEXT,
                    sha256 TEXT,
                    size_bytes INTEGER,
                    content_type TEXT,
                    source_ref TEXT,
                    captured_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(path, artifact_role, sha256)
                )
            ''')
            # Append-only action admission records. An action request may be
            # revalidated many times; every decision receives a distinct ID so
            # evidence and policy history remain replayable.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS action_decisions (
                    id INTEGER PRIMARY KEY,
                    decision_id TEXT UNIQUE NOT NULL,
                    request_id TEXT NOT NULL,
                    action_name TEXT NOT NULL,
                    contract_version TEXT,
                    status TEXT NOT NULL,
                    assurance TEXT,
                    contract_digest TEXT,
                    request_digest TEXT,
                    evidence_digest TEXT,
                    authority_grant_id TEXT,
                    model_fingerprint TEXT NOT NULL DEFAULT '{}',
                    decision_payload TEXT NOT NULL,
                    observed_at DATETIME NOT NULL
                )
            ''')
            # Revocations remain durable even though action grants themselves
            # are portable signed artifacts. A revoked local grant can never be
            # made valid again by editing the artifact.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS action_authority_revocations (
                    id INTEGER PRIMARY KEY,
                    grant_id TEXT UNIQUE NOT NULL,
                    revoked_by TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    revoked_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Public, signed trust-registry transitions are mirrored locally so
            # they appear with action and delivery provenance. The canonical
            # cross-clone record remains .etg/trust.yaml.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trust_registry_events (
                    id INTEGER PRIMARY KEY,
                    event_id TEXT UNIQUE NOT NULL,
                    event_type TEXT NOT NULL,
                    registry_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    approvers TEXT NOT NULL DEFAULT '[]',
                    event_payload TEXT NOT NULL,
                    observed_at DATETIME NOT NULL
                )
            ''')
            # A shared-trust action attestation is a one-time admission token.
            # The unique constraints make the consume-and-record step atomic
            # across concurrent processes using the SQLite ledger.
            conn.execute('''
                CREATE TABLE IF NOT EXISTS action_attestation_consumptions (
                    id INTEGER PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    attestation_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    consumed_at DATETIME NOT NULL,
                    decision_id TEXT NOT NULL UNIQUE,
                    UNIQUE(agent_id, attestation_id),
                    UNIQUE(agent_id, nonce)
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_delivery_evidence_expectation
                ON delivery_evidence(expectation_name, observed_at)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_improvement_proposals_status
                ON improvement_proposals(lifecycle_status, created_at)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_delivery_artifacts_hash
                ON delivery_artifacts(sha256, artifact_role)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_action_decisions_request
                ON action_decisions(request_id, id)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_action_decisions_action_status
                ON action_decisions(action_name, status, id)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_trust_registry_events_observed
                ON trust_registry_events(observed_at, id)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_action_attestation_request
                ON action_attestation_consumptions(agent_id, request_digest)
            ''')
        if self.db_path != ":memory:":
            conn.close()

    def _ensure_columns(self, conn, table: str, columns: Dict[str, str]):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def record_usage_event(
        self,
        *,
        operation: str,
        surface: str,
        input_characters: int,
        output_characters: int,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        lifecycle_state: str = "active",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Records aggregate usage counts without retaining prompt or response content."""
        allowed_metadata = {
            "command",
            "error_code",
            "exit_code",
            "mode",
            "tool",
            "transport",
        }
        sanitized_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if key in allowed_metadata
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    '''
                    INSERT INTO usage_events (
                        operation, surface, input_characters, output_characters,
                        estimated_input_tokens, estimated_output_tokens,
                        lifecycle_state, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        operation,
                        surface,
                        max(0, int(input_characters)),
                        max(0, int(output_characters)),
                        max(0, int(estimated_input_tokens)),
                        max(0, int(estimated_output_tokens)),
                        lifecycle_state if lifecycle_state in {"active", "paused"} else "active",
                        json.dumps(sanitized_metadata, sort_keys=True),
                    ),
                )
                return cursor.lastrowid
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def get_usage_summary(self) -> Dict[str, Any]:
        """Returns all-time and current hydration-session aggregate usage."""
        conn = self._get_connection()
        try:
            boundary = conn.execute(
                '''
                SELECT id, observed_at
                FROM usage_events
                WHERE operation IN ('hydrate', 'boot')
                ORDER BY id DESC
                LIMIT 1
                '''
            ).fetchone()
            all_time = self._usage_aggregate(conn)
            session = self._usage_aggregate(
                conn,
                minimum_id=boundary[0] if boundary else None,
            )
            return {
                "session_boundary": (
                    {"event_id": boundary[0], "observed_at": boundary[1]}
                    if boundary
                    else None
                ),
                "session": session,
                "all_time": all_time,
            }
        finally:
            if self.db_path != ":memory:":
                conn.close()

    def _usage_aggregate(self, conn, minimum_id: Optional[int] = None) -> Dict[str, int]:
        where = "WHERE id >= ?" if minimum_id is not None else ""
        params = (minimum_id,) if minimum_id is not None else ()
        row = conn.execute(
            f'''
            SELECT COUNT(*),
                   COALESCE(SUM(input_characters), 0),
                   COALESCE(SUM(output_characters), 0),
                   COALESCE(SUM(estimated_input_tokens), 0),
                   COALESCE(SUM(estimated_output_tokens), 0)
            FROM usage_events
            {where}
            ''',
            params,
        ).fetchone()
        return {
            "event_count": int(row[0]),
            "input_characters": int(row[1]),
            "output_characters": int(row[2]),
            "estimated_input_tokens": int(row[3]),
            "estimated_output_tokens": int(row[4]),
            "estimated_total_tokens": int(row[3]) + int(row[4]),
        }

    def record_synonym(self, term: str, synonym: str, confidence: float = 1.0) -> bool:
        """Persists a semantic synonym relationship."""
        conn = self._get_connection()
        term = term.lower().strip()
        synonym = synonym.lower().strip()
        try:
            with conn:
                conn.execute('''
                    INSERT INTO synonyms (term, synonym, confidence)
                    VALUES (?, ?, ?)
                    ON CONFLICT(term, synonym) DO UPDATE SET
                        confidence=excluded.confidence,
                        timestamp=CURRENT_TIMESTAMP
                ''', (term, synonym, confidence))
                # Add the inverse relationship as well
                conn.execute('''
                    INSERT INTO synonyms (term, synonym, confidence)
                    VALUES (?, ?, ?)
                    ON CONFLICT(term, synonym) DO UPDATE SET
                        confidence=excluded.confidence,
                        timestamp=CURRENT_TIMESTAMP
                ''', (synonym, term, confidence))
            return True
        except Exception as e:
            print(f"Ledger Error (Synonym): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_synonyms(self, term: Optional[str] = None) -> list:
        """Retrieves synonyms for a given term or all synonyms."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            if term:
                cursor.execute('SELECT synonym, confidence FROM synonyms WHERE term = ?', (term.lower(),))
            else:
                cursor.execute('SELECT term, synonym, confidence FROM synonyms ORDER BY term ASC')
            rows = cursor.fetchall()
            if term:
                return [r[0] for r in rows if r[1] >= 0.5] # Return list of strings above threshold
            return [
                {
                    "term": r[0],
                    "synonym": r[1],
                    "confidence": r[2]
                } for r in rows
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_alignment(
        self,
        source_domain: str,
        target_domain: str,
        source_concept: str,
        target_concept: str,
        confidence: float,
        rationale: str,
        *,
        relation: str = "skos:exactMatch",
        lifecycle_status: str = LIFECYCLE_PROPOSED,
        evidence_type: str = "unreviewed",
        source_artifact: Optional[str] = None,
        verified: bool = False,
        verified_by: Optional[str] = None,
        semantic_confidence: Optional[float] = None,
        schema_confidence: Optional[float] = None,
        data_confidence: Optional[float] = None,
        human_review_confidence: Optional[float] = None,
        runtime_observation_confidence: Optional[float] = None,
    ) -> bool:
        """Persists a semantic alignment with explicit grounding metadata."""
        conn = self._get_connection()
        try:
            verified_at = datetime.now(timezone.utc).isoformat() if verified else None
            with conn:
                conn.execute('''
                    INSERT INTO semantic_alignments (
                        source_domain, target_domain, source_concept, target_concept,
                        relation, confidence, rationale, status, lifecycle_status,
                        evidence_type, source_artifact, verified, verified_by, verified_at,
                        semantic_confidence, schema_confidence, data_confidence,
                        human_review_confidence, runtime_observation_confidence
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_domain, target_domain, source_concept, target_concept) DO UPDATE SET
                        relation=excluded.relation,
                        confidence=excluded.confidence,
                        rationale=excluded.rationale,
                        status=excluded.status,
                        lifecycle_status=excluded.lifecycle_status,
                        evidence_type=excluded.evidence_type,
                        source_artifact=excluded.source_artifact,
                        verified=excluded.verified,
                        verified_by=excluded.verified_by,
                        verified_at=excluded.verified_at,
                        semantic_confidence=excluded.semantic_confidence,
                        schema_confidence=excluded.schema_confidence,
                        data_confidence=excluded.data_confidence,
                        human_review_confidence=excluded.human_review_confidence,
                        runtime_observation_confidence=excluded.runtime_observation_confidence,
                        timestamp=CURRENT_TIMESTAMP
                ''', (
                    source_domain, target_domain, source_concept, target_concept,
                    relation, confidence, rationale,
                    "approved" if verified else "pending",
                    lifecycle_status, evidence_type, source_artifact, int(verified),
                    verified_by if verified else None, verified_at,
                    semantic_confidence, schema_confidence, data_confidence,
                    human_review_confidence, runtime_observation_confidence,
                ))
            return True
        except Exception as e:
            print(f"Ledger Error (Alignment): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_alignment_proposal(
        self,
        source_domain: str,
        target_domain: str,
        source_concept: str,
        target_concept: str,
        confidence: float,
        rationale: str,
        *,
        relation: str = "skos:closeMatch",
        evidence_type: str = "schema_match",
        source_artifact: Optional[str] = None,
    ) -> bool:
        return self.record_alignment(
            source_domain,
            target_domain,
            source_concept,
            target_concept,
            confidence,
            rationale,
            relation=relation,
            lifecycle_status=LIFECYCLE_PROPOSED,
            evidence_type=evidence_type,
            source_artifact=source_artifact,
            verified=False,
            verified_by=None,
            semantic_confidence=confidence,
            schema_confidence=confidence if evidence_type == "schema_match" else None,
        )

    def get_alignments(self, source_domain: Optional[str] = None, trusted_only: bool = False) -> list:
        """Retrieves semantic alignments, optionally restricted to trusted operational alignments."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            query = '''
                SELECT id, source_domain, target_domain, source_concept, target_concept,
                       relation, confidence, rationale, status, timestamp, lifecycle_status,
                       evidence_type, source_artifact, verified, verified_by, verified_at,
                       semantic_confidence, schema_confidence, data_confidence,
                       human_review_confidence, runtime_observation_confidence
                FROM semantic_alignments
            '''
            conditions = []
            params = []
            if source_domain:
                conditions.append("source_domain = ?")
                params.append(source_domain)
            if trusted_only:
                conditions.append("lifecycle_status IN ('reviewed', 'verified')")
                conditions.append("verified = 1")
                conditions.append("confidence >= 0.8")
                conditions.append("evidence_type IN ('human_review', 'integration_test', 'sample_data_match')")
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY timestamp DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "source_domain": r[1],
                    "target_domain": r[2],
                    "source_concept": r[3],
                    "target_concept": r[4],
                    "relation": r[5],
                    "confidence": r[6],
                    "rationale": r[7],
                    "status": r[8],
                    "timestamp": r[9],
                    "lifecycle_status": r[10],
                    "evidence_type": r[11],
                    "source_artifact": r[12],
                    "verified": bool(r[13]),
                    "verified_by": r[14],
                    "verified_at": r[15],
                    "semantic_confidence": r[16],
                    "schema_confidence": r[17],
                    "data_confidence": r[18],
                    "human_review_confidence": r[19],
                    "runtime_observation_confidence": r[20],
                } for r in rows
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_conflict(self, conflict_id: str, entity_type: str, proposed_states: str, source_agents: str) -> bool:
        """Logs a discovered contradiction for human review."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute('''
                    INSERT INTO conflicts (conflict_id, entity_type, proposed_states, source_agents)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(conflict_id) DO UPDATE SET
                        proposed_states=excluded.proposed_states,
                        source_agents=excluded.source_agents,
                        timestamp=CURRENT_TIMESTAMP
                ''', (conflict_id, entity_type, proposed_states, source_agents))
            return True
        except Exception as e:
            print(f"Ledger Error (Conflict): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_resolution(self, conflict_id: str, entity_type: str, state: str, rationale: str) -> bool:
        """Persists a human tie-breaker decision (appends new version) and removes the conflict."""
        conn = self._get_connection()
        try:
            with conn:
                # 1. Get current version
                cursor = conn.cursor()
                cursor.execute('SELECT MAX(version) FROM human_resolutions WHERE conflict_id = ?', (conflict_id,))
                row = cursor.fetchone()
                current_version = row[0] if row[0] is not None else 0
                new_version = current_version + 1

                # 2. Append to resolutions
                conn.execute('''
                    INSERT INTO human_resolutions (conflict_id, entity_type, resolved_state, rationale, version)
                    VALUES (?, ?, ?, ?, ?)
                ''', (conflict_id, entity_type, state, rationale, new_version))
                
                # 3. Remove from pending conflicts
                conn.execute('DELETE FROM conflicts WHERE conflict_id = ?', (conflict_id,))
            return True
        except Exception as e:
            print(f"Ledger Error (Resolution): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_pending_conflicts(self) -> list:
        """Retrieves all conflicts awaiting resolution."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT conflict_id, entity_type, proposed_states, source_agents, timestamp FROM conflicts ORDER BY timestamp DESC')
            rows = cursor.fetchall()
            return [
                {
                    "conflict_id": r[0],
                    "entity_type": r[1],
                    "proposed_states": r[2],
                    "source_agents": r[3],
                    "timestamp": r[4]
                } for r in rows
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_resolution(self, conflict_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the latest decision by its ID."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT resolved_state, rationale, timestamp, version 
                FROM human_resolutions 
                WHERE conflict_id = ? 
                ORDER BY version DESC LIMIT 1
            ''', (conflict_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "state": row[0],
                    "rationale": row[1],
                    "timestamp": row[2],
                    "version": row[3]
                }
            return None
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_all_resolutions(self) -> list:
        """Retrieves all resolutions (latest versions first)."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT r1.conflict_id, r1.entity_type, r1.resolved_state, r1.rationale, r1.timestamp, r1.version
                FROM human_resolutions r1
                INNER JOIN (
                    SELECT conflict_id, MAX(version) as max_version
                    FROM human_resolutions
                    GROUP BY conflict_id
                ) r2 ON r1.conflict_id = r2.conflict_id AND r1.version = r2.max_version
                ORDER BY r1.timestamp DESC
            ''', )
            rows = cursor.fetchall()
            return [
                {
                    "conflict_id": r[0],
                    "entity_type": r[1],
                    "state": r[2],
                    "rationale": r[3],
                    "timestamp": r[4],
                    "version": r[5]
                } for r in rows
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_delivery_evidence(
        self,
        evidence_type: str,
        artifact_ref: str,
        *,
        expectation_name: Optional[str] = None,
        command: Optional[str] = None,
        result_summary: Optional[str] = None,
        passed: bool = True,
        agent_id: Optional[str] = None,
    ) -> Optional[int]:
        """Persists a durable delivery evidence record. Returns the row ID."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute('''
                    INSERT INTO delivery_evidence
                        (evidence_type, expectation_name, artifact_ref, command,
                         result_summary, passed, agent_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    evidence_type, expectation_name, artifact_ref, command,
                    result_summary, int(passed), agent_id,
                ))
                return cursor.lastrowid
        except Exception as e:
            print(f"Ledger Error (DeliveryEvidence): {e}")
            return None
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_action_decision(
        self,
        decision: Dict[str, Any],
        *,
        consume_attestation: bool = True,
    ) -> Optional[int]:
        """Append a complete action-admission decision to the local ledger."""
        required = ("decision_id", "request_id", "action_name", "status", "observed_at")
        missing = [key for key in required if not decision.get(key)]
        if missing:
            raise ValueError(f"action decision is missing required fields: {', '.join(missing)}")
        conn = self._get_connection()
        try:
            with conn:
                if consume_attestation and decision.get("status") == "admitted":
                    identity = decision.get("agent_identity") or {}
                    request = decision.get("request") or {}
                    if identity.get("required") is True:
                        attestation = (decision.get("provenance") or {}).get("agent_attestation") or {}
                        claims = attestation.get("claims") if isinstance(attestation, dict) else None
                        if not isinstance(claims, dict):
                            raise ValueError("admitted shared-trust decision is missing its agent attestation")
                        try:
                            conn.execute(
                                '''
                                INSERT INTO action_attestation_consumptions
                                    (agent_id, attestation_id, nonce, request_digest, consumed_at, decision_id)
                                VALUES (?, ?, ?, ?, ?, ?)
                                ''',
                                (
                                    request["agent_id"],
                                    claims["attestation_id"],
                                    claims["nonce"],
                                    decision["request_digest"],
                                    decision["observed_at"],
                                    decision["decision_id"],
                                ),
                            )
                        except (KeyError, TypeError) as exc:
                            raise ValueError(
                                "admitted shared-trust decision has an incomplete agent attestation"
                            ) from exc
                cursor = conn.execute('''
                    INSERT INTO action_decisions
                        (decision_id, request_id, action_name, contract_version,
                         status, assurance, contract_digest, request_digest,
                         evidence_digest, authority_grant_id, model_fingerprint,
                         decision_payload, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    decision["decision_id"],
                    decision["request_id"],
                    decision["action_name"],
                    decision.get("contract_version"),
                    decision["status"],
                    decision.get("assurance"),
                    decision.get("contract_digest"),
                    decision.get("request_digest"),
                    decision.get("evidence_digest"),
                    (decision.get("authority") or {}).get("grant_id"),
                    json.dumps((decision.get("model") or {}).get("warden_fingerprint", {}), sort_keys=True),
                    json.dumps(decision, sort_keys=True),
                    decision["observed_at"],
                ))
                return cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            if decision.get("status") == "admitted":
                raise ActionAttestationReplayError(
                    "agent attestation or nonce has already been consumed for an admitted action"
                ) from exc
            raise ValueError(f"action decision ID already exists: {decision['decision_id']}") from exc
        finally:
            if self.db_path != ":memory:": conn.close()

    def revoke_action_grant(self, grant_id: str, *, revoked_by: str, rationale: str) -> bool:
        """Persist an irreversible local revocation for a signed authority grant."""
        if not grant_id or not revoked_by or not rationale:
            raise ValueError("grant_id, revoked_by, and rationale are required for revocation")
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO action_authority_revocations (grant_id, revoked_by, rationale) VALUES (?, ?, ?)",
                    (grant_id, revoked_by, rationale),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_action_grant_revocation(self, grant_id: str) -> Optional[Dict[str, Any]]:
        """Return a local grant revocation, if one was recorded."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT grant_id, revoked_by, rationale, revoked_at "
                "FROM action_authority_revocations WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "grant_id": row[0],
                "revoked_by": row[1],
                "rationale": row[2],
                "revoked_at": row[3],
            }
        finally:
            if self.db_path != ":memory:": conn.close()

    def is_action_grant_revoked(self, grant_id: str) -> bool:
        return self.get_action_grant_revocation(grant_id) is not None

    def is_action_attestation_consumed(self, agent_id: str, attestation_id: str, nonce: str) -> bool:
        conn = self._get_connection()
        try:
            return conn.execute(
                """
                SELECT 1 FROM action_attestation_consumptions
                WHERE agent_id = ? AND (attestation_id = ? OR nonce = ?)
                LIMIT 1
                """,
                (agent_id, attestation_id, nonce),
            ).fetchone() is not None
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_trust_registry_event(self, event: Dict[str, Any], *, registry_digest: str) -> Optional[int]:
        """Mirror a signed project-trust transition into the local provenance ledger."""
        required = ("event_id", "event_type", "event_digest", "applied_at")
        missing = [key for key in required if not event.get(key)]
        if missing or not registry_digest:
            if not registry_digest:
                missing.append("registry_digest")
            raise ValueError(f"trust registry event is missing required fields: {', '.join(missing)}")
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute('''
                    INSERT INTO trust_registry_events
                        (event_id, event_type, registry_digest, event_digest, approvers, event_payload, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    event["event_id"],
                    event["event_type"],
                    registry_digest,
                    event["event_digest"],
                    json.dumps(event.get("approvers") or [], sort_keys=True),
                    json.dumps(event, sort_keys=True),
                    event["applied_at"],
                ))
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_action_decisions(
        self,
        *,
        action_name: Optional[str] = None,
        request_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return append-only action decisions with their complete payloads."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        conn = self._get_connection()
        try:
            conditions: List[str] = []
            params: List[Any] = []
            if action_name:
                conditions.append("action_name = ?")
                params.append(action_name)
            if request_id:
                conditions.append("request_id = ?")
                params.append(request_id)
            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                "SELECT id, decision_id, request_id, action_name, contract_version, status, "
                "assurance, contract_digest, request_digest, evidence_digest, authority_grant_id, "
                "model_fingerprint, decision_payload, observed_at "
                f"FROM action_decisions {where} ORDER BY id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "decision_id": row[1],
                    "request_id": row[2],
                    "action_name": row[3],
                    "contract_version": row[4],
                    "status": row[5],
                    "assurance": row[6],
                    "contract_digest": row[7],
                    "request_digest": row[8],
                    "evidence_digest": row[9],
                    "authority_grant_id": row[10],
                    "model_fingerprint": json.loads(row[11] or "{}"),
                    "decision": json.loads(row[12]),
                    "observed_at": row[13],
                }
                for row in rows
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_trust_registry_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return local mirrors of public, signed trust-registry transitions."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, event_id, event_type, registry_digest, event_digest, approvers, event_payload, observed_at "
                "FROM trust_registry_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "event_id": row[1],
                    "event_type": row[2],
                    "registry_digest": row[3],
                    "event_digest": row[4],
                    "approvers": json.loads(row[5] or "[]"),
                    "event": json.loads(row[6]),
                    "observed_at": row[7],
                }
                for row in rows
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_workspace_history(self, *, limit: int = 50, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return a compact, human-oriented timeline across governed records."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        allowed = {"action", "trust", "delivery", "resolution", "improvement", "conflict"}
        if kind and kind not in allowed:
            raise ValueError(f"kind must be one of {', '.join(sorted(allowed))}")
        selected = {kind} if kind else allowed
        events: List[Dict[str, Any]] = []
        conn = self._get_connection()
        try:
            if "action" in selected:
                for row in conn.execute(
                    "SELECT decision_id, action_name, request_id, status, assurance, decision_payload, observed_at "
                    "FROM action_decisions ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall():
                    decision = json.loads(row[5])
                    authority = decision.get("authority") or {}
                    events.append({
                        "event_id": f"action:{row[0]}", "kind": "action", "at": row[6],
                        "actor": authority.get("signer_id") or (decision.get("request") or {}).get("principal"),
                        "agent": (decision.get("request") or {}).get("agent_id"),
                        "target": row[1], "outcome": row[3],
                        "summary": f"{row[1]} request {row[2]}: {row[3]}",
                        "details": decision,
                    })
            if "trust" in selected:
                for row in conn.execute(
                    "SELECT event_id, event_type, approvers, event_payload, observed_at "
                    "FROM trust_registry_events ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall():
                    event = json.loads(row[3])
                    change = event.get("change") or {}
                    subject = change.get("signer_id") or change.get("agent_id") or "unknown subject"
                    events.append({
                        "event_id": f"trust:{row[0]}", "kind": "trust", "at": row[4],
                        "actor": ", ".join(json.loads(row[2] or "[]")), "agent": None,
                        "target": subject, "outcome": row[1],
                        "summary": f"{row[1]} for {subject}",
                        "details": event,
                    })
            if "delivery" in selected:
                for row in conn.execute(
                    "SELECT snapshot_id, expectation_count, missing_proof_count, agent_id, warden_status, metadata, snapped_at "
                    "FROM delivery_snapshots ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall():
                    events.append({
                        "event_id": f"delivery:{row[0]}", "kind": "delivery", "at": row[6],
                        "actor": None, "agent": row[3], "target": row[0],
                        "outcome": "current" if row[2] == 0 and row[4] == "intact" else "attention",
                        "summary": f"Delivery snapshot: {row[1]} expectations, {row[2]} missing proofs",
                        "details": {
                            "snapshot_id": row[0], "expectation_count": row[1],
                            "missing_proof_count": row[2], "warden_status": row[4],
                            "metadata": json.loads(row[5] or "{}"),
                        },
                    })
            if "resolution" in selected:
                for row in conn.execute(
                    "SELECT id, conflict_id, entity_type, resolved_state, rationale, version, timestamp "
                    "FROM human_resolutions ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall():
                    events.append({
                        "event_id": f"resolution:{row[0]}", "kind": "resolution", "at": row[6],
                        "actor": None, "agent": None, "target": row[1], "outcome": "resolved",
                        "summary": f"Resolved {row[1]} (v{row[5]})",
                        "details": {"entity_type": row[2], "resolved_state": row[3], "rationale": row[4]},
                    })
            if "improvement" in selected:
                for row in conn.execute(
                    "SELECT id, title, affected_model, lifecycle_status, created_by, created_at "
                    "FROM improvement_proposals ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall():
                    events.append({
                        "event_id": f"improvement:{row[0]}", "kind": "improvement", "at": row[5],
                        "actor": row[4], "agent": row[4], "target": row[2], "outcome": row[3],
                        "summary": row[1], "details": {"affected_model": row[2]},
                    })
            if "conflict" in selected:
                for row in conn.execute(
                    "SELECT id, conflict_id, entity_type, proposed_states, source_agents, timestamp "
                    "FROM conflicts ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall():
                    events.append({
                        "event_id": f"conflict:{row[0]}", "kind": "conflict", "at": row[5],
                        "actor": ", ".join(json.loads(row[4] or "[]")), "agent": None,
                        "target": row[1], "outcome": "pending", "summary": f"Pending conflict: {row[1]}",
                        "details": {"entity_type": row[2], "proposed_states": json.loads(row[3] or "{}")},
                    })
        finally:
            if self.db_path != ":memory:": conn.close()
        events.sort(key=lambda item: (item.get("at") or "", item["event_id"]), reverse=True)
        return events[:limit]

    def get_provenance_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Return the full evidence payload for one timeline event ID."""
        for event in self.get_workspace_history(limit=10000):
            if event["event_id"] == event_id:
                return event
        return None

    def get_delivery_evidence(
        self,
        expectation_name: Optional[str] = None,
        passed_only: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves delivery evidence records."""
        conn = self._get_connection()
        try:
            conditions, params = [], []
            if expectation_name:
                conditions.append("expectation_name = ?")
                params.append(expectation_name)
            if passed_only:
                conditions.append("passed = 1")
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = conn.execute(
                f"SELECT id, evidence_type, expectation_name, artifact_ref, command, "
                f"result_summary, passed, agent_id, observed_at "
                f"FROM delivery_evidence {where} ORDER BY observed_at DESC LIMIT ?",
                params + [limit],
            )
            return [
                {
                    "id": r[0], "evidence_type": r[1], "expectation_name": r[2],
                    "artifact_ref": r[3], "command": r[4], "result_summary": r[5],
                    "passed": bool(r[6]), "agent_id": r[7], "observed_at": r[8],
                }
                for r in cursor.fetchall()
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_improvement_proposal(
        self,
        title: str,
        affected_model: str,
        proposed_change: Dict[str, Any],
        rationale: str,
        *,
        expected_benefit: Optional[str] = None,
        created_by: Optional[str] = None,
        lifecycle_status: str = "Proposed",
    ) -> Optional[int]:
        """Persists an agent improvement proposal. Returns the row ID."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute('''
                    INSERT INTO improvement_proposals
                        (title, affected_model, proposed_change, rationale,
                         expected_benefit, lifecycle_status, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    title, affected_model, json.dumps(proposed_change), rationale,
                    expected_benefit, lifecycle_status, created_by,
                ))
                return cursor.lastrowid
        except Exception as e:
            print(f"Ledger Error (ImprovementProposal): {e}")
            return None
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_improvement_proposals(
        self,
        lifecycle_status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves improvement proposals."""
        conn = self._get_connection()
        try:
            conditions, params = [], []
            if lifecycle_status:
                conditions.append("lifecycle_status = ?")
                params.append(lifecycle_status)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = conn.execute(
                f"SELECT id, title, affected_model, proposed_change, rationale, "
                f"expected_benefit, lifecycle_status, created_by, created_at "
                f"FROM improvement_proposals {where} ORDER BY created_at DESC LIMIT ?",
                params + [limit],
            )
            rows = []
            for r in cursor.fetchall():
                try:
                    change = json.loads(r[3]) if r[3] else {}
                except (json.JSONDecodeError, TypeError):
                    change = {"raw": r[3]}
                rows.append({
                    "id": r[0], "title": r[1], "affected_model": r[2],
                    "proposed_change": change, "rationale": r[4],
                    "expected_benefit": r[5], "lifecycle_status": r[6],
                    "created_by": r[7], "created_at": r[8],
                })
            return rows
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_lesson(
        self,
        lesson: str,
        *,
        source_task: Optional[str] = None,
        reusable_rule: Optional[str] = None,
        confidence: float = 1.0,
        lifecycle_status: str = "Active",
        agent_id: Optional[str] = None,
    ) -> Optional[int]:
        """Persists a reusable lesson to the ledger (Entigram_Lesson operational path)."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO lessons "
                    "(source_task, lesson, reusable_rule, confidence, lifecycle_status, agent_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (source_task, lesson, reusable_rule, confidence, lifecycle_status, agent_id),
                )
                return cursor.lastrowid
        except Exception as e:
            print(f"Ledger Error (Lesson): {e}")
            return None
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_lessons(
        self,
        lifecycle_status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves recorded lessons."""
        conn = self._get_connection()
        try:
            conditions, params = [], []
            if lifecycle_status:
                conditions.append("lifecycle_status = ?")
                params.append(lifecycle_status)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = conn.execute(
                f"SELECT id, source_task, lesson, reusable_rule, confidence, "
                f"lifecycle_status, agent_id, observed_at "
                f"FROM lessons {where} ORDER BY observed_at DESC LIMIT ?",
                params + [limit],
            )
            return [
                {
                    "id": r[0], "source_task": r[1], "lesson": r[2],
                    "reusable_rule": r[3], "confidence": r[4],
                    "lifecycle_status": r[5], "agent_id": r[6], "observed_at": r[7],
                }
                for r in cursor.fetchall()
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_agent(
        self,
        agent_id: str,
        *,
        agent_class: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        reliability_score: float = 0.5,
        capability_scores: Optional[Dict[str, float]] = None,
        allowed_task_classes: Optional[List[str]] = None,
        restricted_task_classes: Optional[List[str]] = None,
        last_workspace_seen: Optional[str] = None,
        failure_history: Optional[List[Dict[str, Any]]] = None,
        successful_handoffs: int = 0,
        notes: Optional[str] = None,
    ) -> bool:
        """Registers or updates an agent's observed capability profile."""
        conn = self._get_connection()
        try:
            score = max(0.0, min(1.0, float(reliability_score)))
            with conn:
                conn.execute(
                    '''
                    INSERT INTO agent_registry (
                        agent_id, agent_class, provider, model, reliability_score,
                        capability_scores, allowed_task_classes, restricted_task_classes,
                        last_workspace_seen, failure_history, successful_handoffs, notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        agent_class=excluded.agent_class,
                        provider=excluded.provider,
                        model=excluded.model,
                        reliability_score=excluded.reliability_score,
                        capability_scores=excluded.capability_scores,
                        allowed_task_classes=excluded.allowed_task_classes,
                        restricted_task_classes=excluded.restricted_task_classes,
                        last_workspace_seen=excluded.last_workspace_seen,
                        failure_history=excluded.failure_history,
                        successful_handoffs=excluded.successful_handoffs,
                        notes=excluded.notes,
                        updated_at=CURRENT_TIMESTAMP
                    ''',
                    (
                        agent_id,
                        agent_class,
                        provider,
                        model,
                        score,
                        json.dumps(capability_scores or {}, sort_keys=True),
                        json.dumps(allowed_task_classes or [], sort_keys=True),
                        json.dumps(restricted_task_classes or [], sort_keys=True),
                        last_workspace_seen,
                        json.dumps(failure_history or [], sort_keys=True),
                        successful_handoffs,
                        notes,
                    ),
                )
            return True
        except Exception as e:
            print(f"Ledger Error (AgentRegistry): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Returns a registered agent profile."""
        agents = self.get_agents(agent_id=agent_id, limit=1)
        return agents[0] if agents else None

    def get_agents(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves registered agent capability profiles."""
        conn = self._get_connection()
        try:
            conditions, params = [], []
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = conn.execute(
                "SELECT agent_id, agent_class, provider, model, reliability_score, "
                "capability_scores, allowed_task_classes, restricted_task_classes, "
                "last_workspace_seen, failure_history, successful_handoffs, notes, "
                f"created_at, updated_at FROM agent_registry {where} "
                "ORDER BY reliability_score DESC, updated_at DESC LIMIT ?",
                params + [limit],
            )
            return [self._agent_row_to_dict(row) for row in cursor.fetchall()]
        finally:
            if self.db_path != ":memory:": conn.close()

    def enqueue_agent_task(
        self,
        task_id: str,
        title: str,
        task_type: str,
        *,
        risk_level: str = "low_risk",
        required_score: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        status: str = "Queued",
    ) -> bool:
        """Persists a task that can be assigned through capability gating."""
        normalized_risk = self._normalize_risk_level(risk_level)
        minimum = TASK_RISK_REQUIRED_SCORE[normalized_risk]
        score = minimum if required_score is None else max(minimum, min(1.0, float(required_score)))
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    '''
                    INSERT INTO agent_tasks (
                        task_id, title, task_type, risk_level, required_score,
                        details, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        title=excluded.title,
                        task_type=excluded.task_type,
                        risk_level=excluded.risk_level,
                        required_score=excluded.required_score,
                        details=excluded.details,
                        status=excluded.status,
                        updated_at=CURRENT_TIMESTAMP
                    ''',
                    (
                        task_id,
                        title,
                        task_type,
                        normalized_risk,
                        score,
                        json.dumps(details or {}, sort_keys=True),
                        status,
                    ),
                )
            return True
        except Exception as e:
            print(f"Ledger Error (AgentTask): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_agent_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Returns a queued or assigned agent task."""
        tasks = self.get_agent_tasks(task_id=task_id, limit=1)
        return tasks[0] if tasks else None

    def get_agent_tasks(
        self,
        *,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves agent task records."""
        conn = self._get_connection()
        try:
            conditions, params = [], []
            if task_id:
                conditions.append("task_id = ?")
                params.append(task_id)
            if status:
                conditions.append("status = ?")
                params.append(status)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = conn.execute(
                "SELECT task_id, title, task_type, risk_level, required_score, "
                "details, status, assigned_agent_id, assignment_rationale, "
                f"created_at, updated_at FROM agent_tasks {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                params + [limit],
            )
            return [self._task_row_to_dict(row) for row in cursor.fetchall()]
        finally:
            if self.db_path != ":memory:": conn.close()

    def assign_agent_task(self, task_id: str, agent_id: str) -> Dict[str, Any]:
        """Assigns a task only when the agent capability score clears the task risk gate."""
        task = self.get_agent_task(task_id)
        agent = self.get_agent(agent_id)
        if not task:
            return {"ok": False, "reason": "TASK_NOT_FOUND", "task_id": task_id}
        if not agent:
            return {"ok": False, "reason": "AGENT_NOT_REGISTERED", "agent_id": agent_id}

        decision = self.evaluate_agent_assignment(agent, task)
        if not decision["ok"]:
            if decision.get("serious_conflict"):
                self.record_conflict(
                    f"agent-assignment:{task_id}:{agent_id}",
                    "Entigram_Agent_Task",
                    json.dumps({"agent": agent, "task": task, "decision": decision}, sort_keys=True),
                    json.dumps([agent_id, "EntigramBroker"], sort_keys=True),
                )
            return decision

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    '''
                    UPDATE agent_tasks
                    SET status = 'Assigned',
                        assigned_agent_id = ?,
                        assignment_rationale = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                    ''',
                    (agent_id, decision["rationale"], task_id),
                )
            decision.update({"task_id": task_id, "agent_id": agent_id, "status": "Assigned"})
            return decision
        except Exception as e:
            return {"ok": False, "reason": "ASSIGNMENT_WRITE_FAILED", "details": str(e)}
        finally:
            if self.db_path != ":memory:": conn.close()

    def evaluate_agent_assignment(self, agent: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluates whether an agent may receive a task without mutating state."""
        task_type = task["task_type"]
        risk_level = self._normalize_risk_level(task["risk_level"])
        restricted = set(agent.get("restricted_task_classes") or [])
        allowed = set(agent.get("allowed_task_classes") or [])
        capability_scores = agent.get("capability_scores") or {}
        score = float(capability_scores.get(task_type, agent.get("reliability_score", 0.0)))
        required = max(float(task.get("required_score", 0.0)), TASK_RISK_REQUIRED_SCORE[risk_level])

        if task_type in restricted or risk_level in restricted:
            return {
                "ok": False,
                "reason": "TASK_CLASS_RESTRICTED",
                "score": score,
                "required_score": required,
                "serious_conflict": risk_level in {"high_risk", "critical"},
                "rationale": f"{agent['agent_id']} is restricted from {task_type}/{risk_level}.",
            }
        if allowed and "*" not in allowed and task_type not in allowed and risk_level not in allowed:
            return {
                "ok": False,
                "reason": "TASK_CLASS_NOT_ALLOWED",
                "score": score,
                "required_score": required,
                "serious_conflict": risk_level in {"high_risk", "critical"},
                "rationale": f"{agent['agent_id']} is not allow-listed for {task_type}/{risk_level}.",
            }
        if score < required:
            return {
                "ok": False,
                "reason": "CAPABILITY_SCORE_TOO_LOW",
                "score": score,
                "required_score": required,
                "serious_conflict": risk_level in {"high_risk", "critical"},
                "rationale": (
                    f"{agent['agent_id']} scored {score:.2f} for {task_type}; "
                    f"{risk_level} requires {required:.2f}."
                ),
            }
        return {
            "ok": True,
            "reason": "ASSIGNMENT_ALLOWED",
            "score": score,
            "required_score": required,
            "serious_conflict": False,
            "rationale": (
                f"{agent['agent_id']} scored {score:.2f} for {task_type}; "
                f"{risk_level} requires {required:.2f}."
            ),
        }

    def record_agent_hibernation(
        self,
        agent_id: str,
        *,
        hibernate_id: Optional[str] = None,
        run_id: Optional[str] = None,
        status: str = "Hibernated",
        token_threshold: Optional[int] = None,
        remaining_tokens: Optional[int] = None,
        refresh_window_end: Optional[str] = None,
        resume_after: Optional[str] = None,
        checkpoint_summary: Optional[str] = None,
        next_action: Optional[str] = None,
        pending_task_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Persists a durable hibernation checkpoint for external resume scheduling."""
        hibernate_id = hibernate_id or f"hib-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    '''
                    INSERT INTO agent_hibernations (
                        hibernate_id, agent_id, run_id, status, token_threshold,
                        remaining_tokens, refresh_window_end, resume_after,
                        checkpoint_summary, next_action, pending_task_ids
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hibernate_id) DO UPDATE SET
                        agent_id=excluded.agent_id,
                        run_id=excluded.run_id,
                        status=excluded.status,
                        token_threshold=excluded.token_threshold,
                        remaining_tokens=excluded.remaining_tokens,
                        refresh_window_end=excluded.refresh_window_end,
                        resume_after=excluded.resume_after,
                        checkpoint_summary=excluded.checkpoint_summary,
                        next_action=excluded.next_action,
                        pending_task_ids=excluded.pending_task_ids
                    ''',
                    (
                        hibernate_id,
                        agent_id,
                        run_id,
                        status,
                        token_threshold,
                        remaining_tokens,
                        refresh_window_end,
                        resume_after,
                        checkpoint_summary,
                        next_action,
                        json.dumps(pending_task_ids or [], sort_keys=True),
                    ),
                )
            plan = self.get_hibernation(hibernate_id)
            return plan or {"hibernate_id": hibernate_id, "agent_id": agent_id, "status": status}
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_hibernation(self, hibernate_id: str) -> Optional[Dict[str, Any]]:
        """Returns one hibernation checkpoint by ID."""
        plans = self.get_hibernations(hibernate_id=hibernate_id, limit=1)
        return plans[0] if plans else None

    def get_hibernations(
        self,
        *,
        hibernate_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves hibernation checkpoints."""
        conn = self._get_connection()
        try:
            conditions, params = [], []
            if hibernate_id:
                conditions.append("hibernate_id = ?")
                params.append(hibernate_id)
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if status:
                conditions.append("status = ?")
                params.append(status)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            cursor = conn.execute(
                "SELECT hibernate_id, agent_id, run_id, status, token_threshold, "
                "remaining_tokens, refresh_window_end, resume_after, checkpoint_summary, "
                f"next_action, pending_task_ids, created_at, resumed_at FROM agent_hibernations {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                params + [limit],
            )
            return [self._hibernation_row_to_dict(row) for row in cursor.fetchall()]
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_resume_plan(self, agent_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Returns the latest hibernated checkpoint ready for an external scheduler to resume."""
        conn = self._get_connection()
        try:
            conditions = ["status IN ('PreparingHibernate', 'Hibernated', 'ResumeReady')"]
            params: List[Any] = []
            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            cursor = conn.execute(
                "SELECT hibernate_id, agent_id, run_id, status, token_threshold, "
                "remaining_tokens, refresh_window_end, resume_after, checkpoint_summary, "
                "next_action, pending_task_ids, created_at, resumed_at FROM agent_hibernations "
                f"WHERE {' AND '.join(conditions)} ORDER BY created_at DESC, id DESC LIMIT 1",
                params,
            )
            row = cursor.fetchone()
            return self._hibernation_row_to_dict(row) if row else None
        finally:
            if self.db_path != ":memory:": conn.close()

    def mark_hibernation_resumed(self, hibernate_id: str) -> bool:
        """Marks a hibernation checkpoint as resumed."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    "UPDATE agent_hibernations SET status = 'Resumed', resumed_at = CURRENT_TIMESTAMP "
                    "WHERE hibernate_id = ?",
                    (hibernate_id,),
                )
            return cursor.rowcount > 0
        finally:
            if self.db_path != ":memory:": conn.close()

    def _normalize_risk_level(self, risk_level: str) -> str:
        normalized = (risk_level or "low_risk").strip().lower().replace("-", "_")
        if normalized not in TASK_RISK_REQUIRED_SCORE:
            raise ValueError(f"Unknown task risk level: {risk_level}")
        return normalized

    def _agent_row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "agent_id": row[0],
            "agent_class": row[1],
            "provider": row[2],
            "model": row[3],
            "reliability_score": row[4],
            "capability_scores": json.loads(row[5] or "{}"),
            "allowed_task_classes": json.loads(row[6] or "[]"),
            "restricted_task_classes": json.loads(row[7] or "[]"),
            "last_workspace_seen": row[8],
            "failure_history": json.loads(row[9] or "[]"),
            "successful_handoffs": row[10],
            "notes": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }

    def _task_row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "task_id": row[0],
            "title": row[1],
            "task_type": row[2],
            "risk_level": row[3],
            "required_score": row[4],
            "details": json.loads(row[5] or "{}"),
            "status": row[6],
            "assigned_agent_id": row[7],
            "assignment_rationale": row[8],
            "created_at": row[9],
            "updated_at": row[10],
        }

    def _hibernation_row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "hibernate_id": row[0],
            "agent_id": row[1],
            "run_id": row[2],
            "status": row[3],
            "token_threshold": row[4],
            "remaining_tokens": row[5],
            "refresh_window_end": row[6],
            "resume_after": row[7],
            "checkpoint_summary": row[8],
            "next_action": row[9],
            "pending_task_ids": json.loads(row[10] or "[]"),
            "created_at": row[11],
            "resumed_at": row[12],
        }

    def record_delivery_snapshot(
        self,
        snapshot_id: str,
        expectation_count: int,
        missing_proof_count: int,
        *,
        schema_hash: Optional[str] = None,
        agent_id: Optional[str] = None,
        warden_status: Optional[str] = None,
        evidence_ids: Optional[List[int]] = None,
        artifact_ids: Optional[List[int]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persists a frozen delivery snapshot for drift detection."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute('''
                    INSERT INTO delivery_snapshots
                        (snapshot_id, expectation_count, missing_proof_count, schema_hash,
                         agent_id, warden_status, evidence_ids, artifact_ids, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_id) DO UPDATE SET
                        expectation_count=excluded.expectation_count,
                        missing_proof_count=excluded.missing_proof_count,
                        schema_hash=excluded.schema_hash,
                        agent_id=excluded.agent_id,
                        warden_status=excluded.warden_status,
                        evidence_ids=excluded.evidence_ids,
                        artifact_ids=excluded.artifact_ids,
                        metadata=excluded.metadata,
                        snapped_at=CURRENT_TIMESTAMP
                ''', (
                    snapshot_id, expectation_count, missing_proof_count, schema_hash,
                    agent_id, warden_status,
                    json.dumps(evidence_ids or []),
                    json.dumps(artifact_ids or []),
                    json.dumps(metadata or {}),
                ))
            return True
        except Exception as e:
            print(f"Ledger Error (DeliverySnapshot): {e}")
            return False
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent delivery snapshot."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT snapshot_id, expectation_count, missing_proof_count, schema_hash, "
                "agent_id, warden_status, evidence_ids, artifact_ids, metadata, snapped_at "
                "FROM delivery_snapshots ORDER BY snapped_at DESC, id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "snapshot_id": row[0], "expectation_count": row[1],
                "missing_proof_count": row[2], "schema_hash": row[3],
                "agent_id": row[4], "warden_status": row[5],
                "evidence_ids": json.loads(row[6] or "[]"),
                "artifact_ids": json.loads(row[7] or "[]"),
                "metadata": json.loads(row[8] or "{}"),
                "snapped_at": row[9],
            }
        finally:
            if self.db_path != ":memory:": conn.close()

    def record_delivery_artifact(
        self,
        path: str,
        *,
        artifact_role: Optional[str] = None,
        sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        content_type: Optional[str] = None,
        source_ref: Optional[str] = None,
    ) -> Optional[int]:
        """Persists a local artifact reference captured for a delivery."""
        conn = self._get_connection()
        try:
            with conn:
                conn.execute('''
                    INSERT INTO delivery_artifacts
                        (path, artifact_role, sha256, size_bytes, content_type, source_ref)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path, artifact_role, sha256) DO UPDATE SET
                        size_bytes=excluded.size_bytes,
                        content_type=excluded.content_type,
                        source_ref=excluded.source_ref,
                        captured_at=CURRENT_TIMESTAMP
                ''', (path, artifact_role, sha256, size_bytes, content_type, source_ref))
                cursor = conn.execute(
                    "SELECT id FROM delivery_artifacts "
                    "WHERE path = ? AND COALESCE(artifact_role, '') = COALESCE(?, '') "
                    "AND COALESCE(sha256, '') = COALESCE(?, '') "
                    "ORDER BY id DESC LIMIT 1",
                    (path, artifact_role, sha256),
                )
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            print(f"Ledger Error (DeliveryArtifact): {e}")
            return None
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_delivery_artifacts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns recently captured delivery artifacts."""
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT id, path, artifact_role, sha256, size_bytes, content_type, "
                "source_ref, captured_at FROM delivery_artifacts "
                "ORDER BY captured_at DESC, id DESC LIMIT ?",
                (limit,),
            )
            return [
                {
                    "id": r[0],
                    "path": r[1],
                    "artifact_role": r[2],
                    "sha256": r[3],
                    "size_bytes": r[4],
                    "content_type": r[5],
                    "source_ref": r[6],
                    "captured_at": r[7],
                }
                for r in cursor.fetchall()
            ]
        finally:
            if self.db_path != ":memory:": conn.close()

    def get_delivery_artifacts_by_ids(self, artifact_ids: List[int]) -> List[Dict[str, Any]]:
        """Returns delivery artifacts by snapshot-stored row IDs."""
        if not artifact_ids:
            return []
        conn = self._get_connection()
        try:
            placeholders = ",".join("?" for _ in artifact_ids)
            cursor = conn.execute(
                "SELECT id, path, artifact_role, sha256, size_bytes, content_type, "
                f"source_ref, captured_at FROM delivery_artifacts WHERE id IN ({placeholders})",
                artifact_ids,
            )
            rows = {
                r[0]: {
                    "id": r[0],
                    "path": r[1],
                    "artifact_role": r[2],
                    "sha256": r[3],
                    "size_bytes": r[4],
                    "content_type": r[5],
                    "source_ref": r[6],
                    "captured_at": r[7],
                }
                for r in cursor.fetchall()
            }
            return [rows[artifact_id] for artifact_id in artifact_ids if artifact_id in rows]
        finally:
            if self.db_path != ":memory:": conn.close()

    def sync_with_cloud(self, endpoint: str, token: str) -> bool:
        """
        Refuse cloud synchronization until the managed protocol is implemented.

        Previous versions printed a successful upload without performing any
        network operation, which could cause operators to mistake local data for
        a cloud backup.
        """
        resolutions = self.get_all_resolutions()
        conflicts = self.get_pending_conflicts()
        print("❌ [ENTIGRAM CLOUD] Sync is not implemented; no data was uploaded.")
        print(f"   Endpoint requested: {endpoint}")
        print(f"   Local-only state: {len(resolutions)} resolution(s), {len(conflicts)} conflict(s).")
        return False
