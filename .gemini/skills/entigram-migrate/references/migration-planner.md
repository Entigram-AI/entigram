# Migration Planner Reference Checklist

## Objective
Generate complete, executable data migration scripts (Python, SQL, SPARQL) to safely transform existing data from the old LDS/TTL schema version to the new schema version.

## Key Requirements

### 1. Data Transformation Logic
- **Field Renames**: Construct column/property mapping functions (`old_name` -> `new_name`).
- **Field Additions**: Assign default values for newly added `[MUST]` attributes (e.g. default timestamp, placeholder UUID, default string).
- **Type Conversions**: Safe cast functions (e.g., `String` to `Date` parsing, `Integer` to `Decimal` conversion).
- **Entity Splits / Merges**: Transform 1 table/graph entity into multiple entities or vice versa.

### 2. Output Script Types

#### A. SQLite Ledger Database Migration (Python / SQL)
```python
def migrate_sqlite_ledger(db_conn):
    """Up-migration for Entigram SQLite ledger database."""
    cursor = db_conn.cursor()
    # 1. Add new columns
    cursor.execute("ALTER TABLE kpi ADD COLUMN baseline REAL DEFAULT 0.0;")
    # 2. Backfill transformed data
    # 3. Create indices for updated foreign keys
    db_conn.commit()
```

#### B. RDF / TTL Graph Transformation (SPARQL Update / Python rdflib)
```sparql
PREFIX mk: <http://entigram.ai/ontology/custom#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

# Copy renamed property value
INSERT {
  ?s mk:KPI_target_metric ?val .
}
WHERE {
  ?s mk:KPI_target_value ?val .
} ;

# Delete old property value
DELETE {
  ?s mk:KPI_target_value ?val .
}
WHERE {
  ?s mk:KPI_target_value ?val .
} ;
```

#### C. LDS Entity Data Mapper (Python)
- Idempotent transformation script for raw LDS data payloads or JSON instance stores.

## Migration Principles
- **Idempotency**: Scripts can be run multiple times safely without duplicating data.
- **Transaction Safety**: All data modifications wrapped in database transactions or batch atomic operations.
- **Validation Gates**: Assert expected row/triple counts before and after migration.

## Required Output Format for Agent

```markdown
## Migration Planner Results

### Migration Strategy
- Migration Type: Schema Alteration / Full Data Transform / Incremental Patch
- Complexity Level: Low / Medium / High

### Up-Migration Scripts

#### SQLite Ledger Migration (Python/SQL)
```python
# Executable Python script for SQLite ledger update
```

#### TTL / RDF Graph Migration (SPARQL / Python)
```sparql
# Executable SPARQL update query or rdflib script
```

#### Data Transformation Specifications
- `Entity.old_attribute` -> `Entity.new_attribute` (rule: direct copy)
- `Entity.new_required_field` (rule: set default 'N/A')
```
