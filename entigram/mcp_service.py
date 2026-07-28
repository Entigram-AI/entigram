import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from entigram.broker import EntigramBroker
from entigram.schema_compiler.parser import SchemaParser
from entigram.governance.algebra import RelationalAlgebraGuard
from entigram.workspace_contract import configured_schema_paths

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")
_CONCEPT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
_ERROR_LABELS = {
    "alignment": "Invalid Schema Alignment",
    "conflict": "Invalid Conflict",
    "schema": "Schema Discovery Failed",
    "impact": "Impact Analysis Failed",
}


class EntigramMCPService:
    """
    Deterministic service layer behind the MCP server.

    All payloads are treated as untrusted. The service accepts only strict JSON
    objects, validates concepts against local LDS schemas, and writes through
    parameterized ledger APIs.
    """

    def __init__(self, target_dir: str = "."):
        self.target_dir = Path(target_dir).expanduser().resolve()

    def get_schemas(self) -> str:
        try:
            schemas = []
            for path in self._schema_paths():
                text = path.read_text()
                entities, relationships = SchemaParser(text).parse()
                schemas.append(
                    {
                        "path": self._relative_path(path),
                        "entities": {
                            name: {
                                "attributes": [
                                    {
                                        "name": attr["name"],
                                        "type": attr["type"],
                                        "pk": bool(attr.get("pk")),
                                        "constraints": attr.get("constraints", []),
                                    }
                                    for attr in entity.attributes
                                ],
                                "external_ref": entity.external_ref,
                            }
                            for name, entity in entities.items()
                        },
                        "relationships": [
                            {
                                "entity_a": rel.entity_a,
                                "degree_a": rel.degree_a,
                                "part_a": rel.part_a,
                                "entity_b": rel.entity_b,
                                "degree_b": rel.degree_b,
                                "part_b": rel.part_b,
                            }
                            for rel in relationships
                        ],
                        "raw": text,
                    }
                )
            return json.dumps({"ok": True, "schemas": schemas}, indent=2, sort_keys=True)
        except Exception as exc:
            return self._error("schema", "SCHEMA_DISCOVERY_FAILED", str(exc))

    def get_impact(self, file_path: str) -> str:
        try:
            broker = EntigramBroker(str(self.target_dir))
            impact = broker.analyze_impact(file_path)
            return json.dumps({"ok": True, "impact": impact}, indent=2, sort_keys=True)
        except Exception as exc:
            return self._error("impact", "IMPACT_ANALYSIS_FAILED", str(exc))

    def propose_alignment(self, payload: Any) -> str:
        data, error = self._coerce_json_object(payload)
        if error:
            return self._alignment_error_from_message(error, "INVALID_JSON")

        allowed = {
            "source_domain",
            "target_domain",
            "source_concept",
            "target_concept",
            "confidence",
            "rationale",
            "relation",
            "source_artifact",
        }
        error = self._reject_unknown_keys(data, allowed)
        if error:
            return self._alignment_error_from_message(error, "UNKNOWN_FIELD")

        required = [
            "source_domain",
            "target_domain",
            "source_concept",
            "target_concept",
            "rationale",
        ]
        error = self._require_keys(data, required)
        if error:
            return self._alignment_error_from_message(error, "MISSING_FIELD")

        for key in ("source_domain", "target_domain"):
            error = self._validate_identifier(key, data[key])
            if error:
                return self._alignment_error_from_message(error, "INVALID_IDENTIFIER")

        try:
            catalog = self._schema_catalog()
        except Exception as exc:
            return self._error("alignment", "SCHEMA_CATALOG_FAILED", str(exc))
        for key in ("source_concept", "target_concept"):
            error = self._validate_concept_value(key, data[key], catalog)
            if error:
                return self._alignment_error_from_message(error, "UNKNOWN_CONCEPT")

        confidence, error = self._validate_confidence(data.get("confidence", 1.0))
        if error:
            return self._alignment_error_from_message(error, "INVALID_CONFIDENCE")

        rationale = data["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            return self._error("alignment", "INVALID_RATIONALE", "rationale must be a non-empty string")
        if len(rationale) > 2000:
            return self._error("alignment", "INVALID_RATIONALE", "rationale exceeds 2000 characters")

        relation = data.get("relation", "skos:closeMatch")
        if not isinstance(relation, str) or relation not in {
            "skos:exactMatch",
            "skos:closeMatch",
            "skos:relatedMatch",
        }:
            return self._error("alignment", "INVALID_RELATION", "relation is not allowed")

        source_artifact = data.get("source_artifact")
        if source_artifact is not None and not isinstance(source_artifact, str):
            return self._error("alignment", "INVALID_SOURCE_ARTIFACT", "source_artifact must be a string")

        broker = EntigramBroker(str(self.target_dir))
        if not broker.warden.verify_integrity():
            return self._error("alignment", "SCHEMA_INTEGRITY_FAILED", "schema integrity check failed")

        try:
            RelationalAlgebraGuard(catalog, broker).validate_alignment_proposal(
                data["source_domain"],
                data["target_domain"],
                data["source_concept"],
                data["target_concept"],
            )
        except ValueError as e:
            return self._error("alignment", "RELATIONAL_GUARD_FAILED", str(e))

        ok = broker.ledger.record_alignment_proposal(
            source_domain=data["source_domain"],
            target_domain=data["target_domain"],
            source_concept=data["source_concept"],
            target_concept=data["target_concept"],
            confidence=confidence,
            rationale=rationale.strip(),
            relation=relation,
            evidence_type="schema_match",
            source_artifact=source_artifact,
        )
        if not ok:
            return self._error("alignment", "LEDGER_WRITE_FAILED", "ledger write failed")

        return json.dumps(
            {
                "ok": True,
                "status": "proposed",
                "source_domain": data["source_domain"],
                "target_domain": data["target_domain"],
                "source_concept": data["source_concept"],
                "target_concept": data["target_concept"],
            },
            sort_keys=True,
        )

    def log_conflict(self, payload: Any) -> str:
        data, error = self._coerce_json_object(payload)
        if error:
            return self._conflict_error_from_message(error, "INVALID_JSON")

        allowed = {"conflict_id", "entity_type", "proposed_states", "agent_id"}
        error = self._reject_unknown_keys(data, allowed)
        if error:
            return self._conflict_error_from_message(error, "UNKNOWN_FIELD")

        error = self._require_keys(data, ["conflict_id", "entity_type", "proposed_states", "agent_id"])
        if error:
            return self._conflict_error_from_message(error, "MISSING_FIELD")

        for key in ("conflict_id", "entity_type", "agent_id"):
            error = self._validate_identifier(key, data[key])
            if error:
                return self._conflict_error_from_message(error, "INVALID_IDENTIFIER")

        proposed_states = data["proposed_states"]
        if not isinstance(proposed_states, dict) or not proposed_states:
            return self._error("conflict", "INVALID_PROPOSED_STATES", "proposed_states must be a non-empty object")

        try:
            catalog = self._schema_catalog()
        except Exception as exc:
            return self._error("conflict", "SCHEMA_CATALOG_FAILED", str(exc))
        entity_error = self._validate_entity(data["entity_type"], catalog)
        if entity_error:
            return self._conflict_error_from_message(entity_error, "UNKNOWN_ENTITY")

        allowed_attrs = catalog["entities"][data["entity_type"]]
        for agent, state in proposed_states.items():
            if not isinstance(agent, str) or not _IDENTIFIER_RE.match(agent):
                return self._error("conflict", "INVALID_AGENT_ID", "proposed_states contains an invalid agent id")
            if not isinstance(state, dict):
                return self._error("conflict", "INVALID_AGENT_STATE", f"state for agent {agent} must be an object")
            for attr in state:
                if attr not in allowed_attrs:
                    return self._error(
                        "conflict",
                        "UNKNOWN_ATTRIBUTE",
                        f"Attribute {attr} not found on entity {data['entity_type']}",
                    )

        broker = EntigramBroker(str(self.target_dir))
        if not broker.warden.verify_integrity():
            return self._error("conflict", "SCHEMA_INTEGRITY_FAILED", "schema integrity check failed")

        ok = broker.ledger.record_conflict(
            conflict_id=data["conflict_id"],
            entity_type=data["entity_type"],
            proposed_states=json.dumps(proposed_states, sort_keys=True),
            source_agents=json.dumps([data["agent_id"]], sort_keys=True),
        )
        if not ok:
            return self._error("conflict", "LEDGER_WRITE_FAILED", "ledger write failed")

        return json.dumps(
            {
                "ok": True,
                "status": "logged",
                "conflict_id": data["conflict_id"],
                "entity_type": data["entity_type"],
            },
            sort_keys=True,
        )

    def _schema_paths(self) -> List[Path]:
        configured = self._configured_schema_paths()
        if configured is not None:
            return configured

        ignored = {".git", ".etg", ".venv", "venv", "__pycache__", "build", "dist"}
        paths = []
        for path in sorted(self.target_dir.rglob("*.lds")):
            if any(part in ignored for part in path.relative_to(self.target_dir).parts):
                continue
            if path.is_file():
                paths.append(path)
        return paths

    def _configured_schema_paths(self) -> Optional[List[Path]]:
        return configured_schema_paths(self.target_dir)

    def _schema_catalog(self) -> Dict[str, Any]:
        entities: Dict[str, set] = {}
        relationships = []
        schema_count = 0
        for path in self._schema_paths():
            schema_count += 1
            parsed, rels = SchemaParser(path.read_text()).parse()
            for name, entity in parsed.items():
                attrs = entities.setdefault(name, set())
                attrs.update(attr["name"] for attr in entity.attributes)
            for r in rels:
                relationships.append({
                    "entity_a": r.entity_a,
                    "degree_a": r.degree_a,
                    "part_a": r.part_a,
                    "entity_b": r.entity_b,
                    "degree_b": r.degree_b,
                    "part_b": r.part_b,
                })
        return {"schema_count": schema_count, "entities": entities, "relationships": relationships}

    def _relative_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.target_dir).as_posix()
        except ValueError:
            return str(path)

    def _coerce_json_object(self, payload: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if isinstance(payload, str):
            try:
                data = json.loads(
                    payload,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"Invalid JSON constant: {value}")
                    ),
                    object_pairs_hook=self._reject_duplicate_keys,
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return None, f"Error: Invalid Schema Alignment - payload is not strict JSON: {exc}"
        elif isinstance(payload, dict):
            data = payload
        else:
            return None, "Error: Invalid Schema Alignment - payload must be a JSON object"

        if not isinstance(data, dict):
            return None, "Error: Invalid Schema Alignment - payload must be a JSON object"
        return data, None

    def _reject_duplicate_keys(self, pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def _reject_unknown_keys(self, data: Mapping[str, Any], allowed: set) -> Optional[str]:
        unknown = sorted(set(data.keys()) - allowed)
        if unknown:
            return f"Error: Invalid Schema Alignment - unknown field(s): {', '.join(unknown)}"
        return None

    def _require_keys(self, data: Mapping[str, Any], required: List[str]) -> Optional[str]:
        missing = [key for key in required if key not in data]
        if missing:
            return f"Error: Invalid Schema Alignment - missing required field(s): {', '.join(missing)}"
        return None

    def _validate_identifier(self, field: str, value: Any) -> Optional[str]:
        if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
            return f"Error: Invalid Schema Alignment - {field} must be a safe identifier"
        return None

    def _validate_confidence(self, value: Any) -> Tuple[Optional[float], Optional[str]]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, "Error: Invalid Schema Alignment - confidence must be a number"
        confidence = float(value)
        if confidence < 0.0 or confidence > 1.0:
            return None, "Error: Invalid Schema Alignment - confidence must be between 0 and 1"
        return confidence, None

    def _validate_concept_value(self, field: str, value: Any, catalog: Dict[str, Any]) -> Optional[str]:
        if not isinstance(value, str) or not _CONCEPT_RE.match(value):
            return f"Error: Invalid Schema Alignment - {field} must be Entity or Entity.attribute"

        entity_name, _, attr_name = value.partition(".")
        entity_error = self._validate_entity(entity_name, catalog)
        if entity_error:
            return entity_error
        if attr_name and attr_name not in catalog["entities"][entity_name]:
            return f"Error: Invalid Schema Alignment - Attribute {attr_name} not found on entity {entity_name}"
        return None

    def _validate_entity(self, entity_name: str, catalog: Dict[str, Any]) -> Optional[str]:
        if catalog["schema_count"] == 0:
            return "Error: Invalid Schema Alignment - no LDS schemas found"
        if entity_name not in catalog["entities"]:
            return f"Error: Invalid Schema Alignment - Entity {entity_name} not found"
        return None

    def _alignment_error_from_message(self, message: str, code: str) -> str:
        return self._error("alignment", code, self._error_detail(message, "alignment"))

    def _conflict_error_from_message(self, message: str, code: str) -> str:
        return self._error("conflict", code, self._error_detail(message, "conflict"))

    def _error_detail(self, message: str, kind: str) -> str:
        prefix = f"Error: {_ERROR_LABELS[kind]} - "
        if message.startswith(prefix):
            return message[len(prefix):]
        alignment_prefix = "Error: Invalid Schema Alignment - "
        if message.startswith(alignment_prefix):
            return message[len(alignment_prefix):]
        return message

    def _error(self, kind: str, code: str, detail: str) -> str:
        label = _ERROR_LABELS[kind]
        message = f"Error: {label} - {detail}"
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": code,
                    "message": message,
                    "details": detail,
                },
            },
            sort_keys=True,
        )
