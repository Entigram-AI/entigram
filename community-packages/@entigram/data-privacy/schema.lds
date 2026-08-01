ENTITY: Privacy_Profile
ATTRIBUTES:
  . id (UUID)
  - profile_key (String, MUST, UNIQUE)
  - subject_ref (String, MUST)
  - assessed_at (DateTime)

ENTITY: Data_Asset
ATTRIBUTES:
  . id (UUID)
  - name (String, MUST)
  - owner (String)
  - system_ref (String)

ENTITY: Data_Field
ATTRIBUTES:
  . id (UUID)
  - name (String, MUST)
  - classification (String)
  - pii_category (String)
  - encrypted_at_rest (Boolean)
  - retention_days (Integer)

ENTITY: Privacy_Control
ATTRIBUTES:
  . id (UUID)
  - control_type (String, MUST)
  - scope (String, MUST)
  - status (String, MUST)
  - evidence_ref (String)

ENTITY: Privacy_Evidence
ATTRIBUTES:
  . id (UUID)
  - evidence_ref (String, MUST)
  - evidence_type (String)
  - observed_at (DateTime)

ENTITY: Privacy_Finding
ATTRIBUTES:
  . id (UUID)
  - code (String, MUST)
  - severity (String, MUST)
  - message (String, MUST)
  - recommendation (String)

RELATIONSHIPS:
- Privacy_Profile (1) [MUST] --- [MAY] (MANY) Data_Asset
- Data_Asset (1) [MUST] --- [MAY] (MANY) Data_Field
- Privacy_Profile (1) [MUST] --- [MAY] (MANY) Privacy_Control
- Privacy_Control (1) [MAY] --- [MAY] (MANY) Privacy_Evidence
- Privacy_Profile (1) [MUST] --- [MAY] (MANY) Privacy_Finding
