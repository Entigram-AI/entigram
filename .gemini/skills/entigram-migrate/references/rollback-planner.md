# Rollback Planner Reference Checklist

## Objective
Generate down-migration scripts and contingency procedures to safely revert the schema, ontology, and instance data back to the previous version if a migration fails or must be aborted.

## Key Requirements

### 1. Down-Migration Scripts (Reverse Transformations)

#### A. SQLite Ledger Down-Migration (Python / SQL)
```python
def rollback_sqlite_ledger(db_conn):
    """Down-migration / Rollback for Entigram SQLite ledger database."""
    cursor = db_conn.cursor()
    # Revert added columns or restore backup table
    cursor.execute("ALTER TABLE kpi DROP COLUMN baseline;")
    db_conn.commit()
```

#### B. TTL / RDF Graph Down-Migration (SPARQL / Python)
```sparql
PREFIX mk: <http://entigram.ai/ontology/custom#>

# Revert property rename
INSERT {
  ?s mk:KPI_target_value ?val .
}
WHERE {
  ?s mk:KPI_target_metric ?val .
} ;

DELETE {
  ?s mk:KPI_target_metric ?val .
}
WHERE {
  ?s mk:KPI_target_metric ?val .
} ;
```

### 2. Failure Recovery Procedures
1. **Pre-Migration Snapshot**: Step to create full backup of `.lds`, `.ttl`, and `.sqlite` ledger database prior to executing up-migration.
2. **Atomic Abort Sequence**: Commands to roll back open database transactions and restore files from snapshot.
3. **Data Loss Assessment**: Identify any newly added data during migration windows that may require export prior to rollback.

## Required Output Format for Agent

```markdown
## Rollback Planner Results

### Rollback Strategy
- Complexity: Low / Medium / High
- Data Retention: Full / Partial (if non-reversible fields dropped)

### Down-Migration Scripts

#### SQLite Ledger Down-Migration (Python/SQL)
```python
# Executable Python script for SQLite ledger rollback
```

#### TTL / RDF Graph Down-Migration (SPARQL / Python)
```sparql
# Executable SPARQL update query for ontology graph rollback
```

### Step-by-Step Rollback Procedure
1. Halt write operations to ledger and graph store.
2. Restore `.lds` schema file from `schema.lds.bak`.
3. Restore `.ttl` ontology file from `schema.ttl.bak`.
4. Execute `rollback_sqlite_ledger` or restore SQLite database snapshot.
5. Verify schema compiler and graph builder pass on reverted files.
```
