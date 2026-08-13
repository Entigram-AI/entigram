/*
 * Entigram meta-schema
 * Canonical closed-world schema for an Entigram workspace.
 */

ENTITY: kind_of_thing {
    name String PK
}

ENTITY: descriptor {
    id UUID PK
}

ENTITY: entity {
    id UUID PK
}

ENTITY: attribute {
    id UUID PK
    scale String
}

ENTITY: relationship {
    id UUID PK
    to_be_flag Boolean
}

ENTITY: link {
    id UUID PK
    max_degree String
    label String
}

ENTITY: identifier {
    id UUID PK
    symbol String
}


/* --- Meta-Relationships --- */

/* Super/Sub Types (Represented structurally) */
RELATIONSHIP: kind_of_thing (1) [MAY] --- [MUST] (1) entity
RELATIONSHIP: kind_of_thing (1) [MAY] --- [MUST] (1) relationship
RELATIONSHIP: kind_of_thing (1) [MAY] --- [MUST] (1) attribute
RELATIONSHIP: kind_of_thing (1) [MAY] --- [MUST] (1) link

RELATIONSHIP: descriptor (1) [MAY] --- [MUST] (1) entity
RELATIONSHIP: descriptor (1) [MAY] --- [MUST] (1) attribute
RELATIONSHIP: descriptor (1) [MAY] --- [MUST] (1) link


/* Core Links (Fragment 23-4) */
RELATIONSHIP: entity (1) [MAY] --- [MUST] (MANY) descriptor
RELATIONSHIP: entity (1) [MUST] --- [MUST] (MANY) identifier
RELATIONSHIP: descriptor (1) [MAY] --- [MUST] (MANY) identifier
RELATIONSHIP: relationship (1) [MUST] --- [MUST] (MANY) link
