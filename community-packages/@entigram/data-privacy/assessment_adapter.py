"""Offline metadata-only assessment for privacy controls."""

from __future__ import annotations

import fnmatch
import re

from entigram.assessment import AssessmentAdapter, AssessmentFinding, AssessmentResult


MAX_ASSETS = 500
MAX_FIELDS = 5000
MAX_RULES = 500
MAX_TEXT_LENGTH = 4096
MAX_REFERENCE_LENGTH = 2048
MAX_RETENTION_DAYS = 36500

CLASSIFICATIONS = {
    "public",
    "internal",
    "personal",
    "sensitive_personal",
    "special_category",
    "unknown",
}

PII_NAME_PATTERNS = {
    "address": ("address", "street", "postal", "zip", "city"),
    "contact": ("email", "e_mail", "phone", "telephone", "mobile"),
    "government_id": ("ssn", "social_security", "passport", "driver_license", "tax_id"),
    "name": ("first_name", "last_name", "full_name", "customer_name", "person_name"),
    "financial": ("iban", "bank_account", "routing_number", "card_number"),
    "location": ("latitude", "longitude", "geo_location"),
    "health": ("diagnosis", "medical", "health", "medication"),
}

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class DataPrivacyAssessmentAdapter(AssessmentAdapter):
    """Review metadata and evidence without accessing data values."""

    name = "data-privacy-assessment"
    capabilities = (
        "pii-detection/v1",
        "encryption-audit/v1",
        "retention-policy-check/v1",
    )

    def assess(self, subject):
        if subject.subject_type != "data-privacy-profile":
            raise ValueError(
                "data-privacy-assessment requires subject_type data-privacy-profile"
            )
        data = subject.data
        allowed = {"assets", "controls", "custom_rules", "profile_key"}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError("data privacy data contains unknown fields: " + ", ".join(unknown))

        assets = data.get("assets", [])
        controls = data.get("controls", [])
        custom_rules = data.get("custom_rules", [])
        self._validate_collection(assets, "assets", MAX_ASSETS)
        self._validate_collection(controls, "controls", MAX_ASSETS)
        self._validate_collection(custom_rules, "custom_rules", MAX_RULES)
        self._validate_optional_text(data.get("profile_key"), "profile_key")

        fields = []
        for asset in assets:
            fields.extend(self._validate_asset(asset))
        if len(fields) > MAX_FIELDS:
            raise ValueError(f"fields exceeds {MAX_FIELDS} entries")
        normalized_controls = [self._validate_control(control) for control in controls]
        normalized_rules = [self._validate_rule(rule) for rule in custom_rules]

        findings = []
        pii_count = 0
        encrypted_count = 0
        retention_count = 0
        for field in fields:
            likely_categories = self._likely_pii_categories(field["name"])
            classification = field.get("classification", "unknown")
            likely_pii = bool(likely_categories) or classification in {
                "personal",
                "sensitive_personal",
                "special_category",
            }
            if not likely_pii:
                continue
            pii_count += 1
            if classification == "unknown":
                findings.append(
                    self._finding(
                        "PII_FIELD_UNCLASSIFIED",
                        "medium",
                        "Likely personal-data field is unclassified",
                        f"{field['name']} matches metadata-only personal-data heuristics but has no classification.",
                        "Review the field without exposing values and record its approved classification.",
                        evidence={"field": field["name"], "likely_categories": likely_categories},
                    )
                )
            if field.get("encrypted_at_rest") is not True:
                findings.append(
                    self._finding(
                        "ENCRYPTION_CONTROL_MISSING",
                        "high",
                        "Personal-data field lacks an encryption-at-rest control",
                        f"{field['name']} is likely personal data and is not marked encrypted_at_rest.",
                        "Document an approved encryption-at-rest control or record an explicitly accepted risk.",
                        evidence={"field": field["name"]},
                    )
                )
            else:
                encrypted_count += 1
                if not field.get("encryption_evidence_ref"):
                    findings.append(
                        self._finding(
                            "ENCRYPTION_EVIDENCE_MISSING",
                            "medium",
                            "Encryption control lacks evidence",
                            f"{field['name']} is marked encrypted but has no evidence reference.",
                            "Attach a durable reference to the key, storage, or control evidence.",
                            evidence={"field": field["name"]},
                        )
                    )
            has_retention = (
                isinstance(field.get("retention_days"), int)
                or bool(field.get("retention_policy_ref"))
            )
            if not has_retention:
                findings.append(
                    self._finding(
                        "RETENTION_POLICY_MISSING",
                        "high",
                        "Personal-data field lacks a retention policy",
                        f"{field['name']} has no retention_days or retention_policy_ref.",
                        "Define a documented retention period or reference an approved retention policy.",
                        evidence={"field": field["name"]},
                    )
                )
            else:
                retention_count += 1
                if not field.get("retention_evidence_ref"):
                    findings.append(
                        self._finding(
                            "RETENTION_EVIDENCE_MISSING",
                            "medium",
                            "Retention control lacks evidence",
                            f"{field['name']} has a retention control but no evidence reference.",
                            "Attach a durable reference to the retention policy or deletion-control evidence.",
                            evidence={"field": field["name"]},
                        )
                    )

        findings.extend(self._assess_controls(normalized_controls))
        findings.extend(self._apply_custom_rules(fields, normalized_rules))

        return AssessmentResult(
            adapter=self.name,
            subject=subject,
            capabilities=list(self.capabilities),
            findings=findings,
            metadata={
                "assessment_kind": "metadata_privacy_controls",
                "offline": True,
                "raw_values_accessed": False,
                "heuristic_pii_detection": True,
                "assets_assessed": len(assets),
                "fields_assessed": len(fields),
                "likely_pii_fields": pii_count,
                "encrypted_likely_pii_fields": encrypted_count,
                "retention_controlled_likely_pii_fields": retention_count,
                "custom_rules_applied": len(normalized_rules),
                "compliance_proven": False,
            },
        )

    def _validate_asset(self, asset):
        self._require_object(asset, "asset")
        allowed = {"name", "owner", "system_ref", "fields"}
        unknown = sorted(set(asset) - allowed)
        if unknown:
            raise ValueError("asset contains unknown fields: " + ", ".join(unknown))
        self._require_text(asset.get("name"), "asset.name")
        self._validate_optional_text(asset.get("owner"), "asset.owner")
        self._validate_optional_text(asset.get("system_ref"), "asset.system_ref")
        fields = asset.get("fields", [])
        if not isinstance(fields, list):
            raise ValueError("asset.fields must be a list")
        return [self._validate_field(field, asset["name"]) for field in fields]

    def _validate_field(self, field, asset_name):
        self._require_object(field, "field")
        allowed = {
            "name", "data_type", "classification", "pii_category",
            "encrypted_at_rest", "encryption_evidence_ref", "retention_days",
            "retention_policy_ref", "retention_evidence_ref", "evidence_refs",
        }
        unknown = sorted(set(field) - allowed)
        if unknown:
            raise ValueError("field contains unknown fields: " + ", ".join(unknown))
        normalized = dict(field)
        self._require_text(field.get("name"), "field.name")
        normalized["name"] = f"{asset_name}.{field['name']}"
        self._validate_optional_text(field.get("data_type"), "field.data_type")
        classification = field.get("classification", "unknown")
        if classification not in CLASSIFICATIONS:
            raise ValueError("field.classification must be one of " + ", ".join(sorted(CLASSIFICATIONS)))
        if "pii_category" in field:
            self._validate_optional_text(field["pii_category"], "field.pii_category")
        if "encrypted_at_rest" in field and not isinstance(field["encrypted_at_rest"], bool):
            raise ValueError("field.encrypted_at_rest must be boolean")
        for key in ("encryption_evidence_ref", "retention_policy_ref", "retention_evidence_ref"):
            self._validate_optional_reference(field.get(key), f"field.{key}")
        if "retention_days" in field:
            value = field["retention_days"]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RETENTION_DAYS:
                raise ValueError(f"field.retention_days must be an integer from 0 to {MAX_RETENTION_DAYS}")
        evidence_refs = field.get("evidence_refs", [])
        self._validate_references(evidence_refs, "field.evidence_refs")
        return normalized

    def _validate_control(self, control):
        self._require_object(control, "control")
        allowed = {"control_type", "scope", "status", "evidence_ref"}
        unknown = sorted(set(control) - allowed)
        if unknown:
            raise ValueError("control contains unknown fields: " + ", ".join(unknown))
        for key in ("control_type", "scope", "status"):
            self._require_text(control.get(key), f"control.{key}")
        self._validate_optional_reference(control.get("evidence_ref"), "control.evidence_ref")
        return dict(control)

    def _validate_rule(self, rule):
        self._require_object(rule, "custom rule")
        allowed = {"id", "field_pattern", "require_classification", "require_encryption", "require_retention"}
        unknown = sorted(set(rule) - allowed)
        if unknown:
            raise ValueError("custom rule contains unknown fields: " + ", ".join(unknown))
        self._require_text(rule.get("id"), "custom_rule.id")
        if not SAFE_IDENTIFIER.fullmatch(rule["id"]):
            raise ValueError("custom_rule.id is not a safe identifier")
        self._require_text(rule.get("field_pattern"), "custom_rule.field_pattern")
        if len(rule["field_pattern"]) > 256:
            raise ValueError("custom_rule.field_pattern exceeds safe text limits")
        if "require_classification" in rule and rule["require_classification"] not in CLASSIFICATIONS - {"unknown"}:
            raise ValueError("custom_rule.require_classification is invalid")
        for key in ("require_encryption", "require_retention"):
            if key in rule and not isinstance(rule[key], bool):
                raise ValueError(f"custom_rule.{key} must be boolean")
        return dict(rule)

    def _assess_controls(self, controls):
        findings = []
        for control in controls:
            status = control["status"].lower()
            if status in {"missing", "not_implemented", "failed"}:
                findings.append(
                    self._finding(
                        "PRIVACY_CONTROL_GAP",
                        "high",
                        "Privacy control is not implemented",
                        f"{control['control_type']} for {control['scope']} is marked {control['status']}.",
                        "Assign an owner and remediation plan or document an accepted risk.",
                        evidence={"control_type": control["control_type"], "scope": control["scope"]},
                    )
                )
            elif status in {"partial", "unknown"}:
                findings.append(
                    self._finding(
                        "PRIVACY_CONTROL_INCOMPLETE",
                        "medium",
                        "Privacy control is incomplete or unknown",
                        f"{control['control_type']} for {control['scope']} is marked {control['status']}.",
                        "Collect evidence and define the remaining implementation work.",
                        evidence={"control_type": control["control_type"], "scope": control["scope"]},
                    )
                )
            elif status == "implemented" and not control.get("evidence_ref"):
                findings.append(
                    self._finding(
                        "PRIVACY_CONTROL_EVIDENCE_MISSING",
                        "medium",
                        "Implemented privacy control lacks evidence",
                        f"{control['control_type']} for {control['scope']} is marked implemented without evidence.",
                        "Attach a durable evidence reference for the implemented control.",
                        evidence={"control_type": control["control_type"], "scope": control["scope"]},
                    )
                )
        return findings

    def _apply_custom_rules(self, fields, rules):
        findings = []
        for rule in rules:
            for field in fields:
                if not fnmatch.fnmatchcase(field["name"], rule["field_pattern"]):
                    continue
                if rule.get("require_classification") and field.get("classification") != rule["require_classification"]:
                    findings.append(self._custom_finding(rule, field, "classification"))
                if rule.get("require_encryption") and field.get("encrypted_at_rest") is not True:
                    findings.append(self._custom_finding(rule, field, "encryption"))
                if rule.get("require_retention") and not (
                    isinstance(field.get("retention_days"), int) or field.get("retention_policy_ref")
                ):
                    findings.append(self._custom_finding(rule, field, "retention"))
        return findings

    def _custom_finding(self, rule, field, missing_control):
        return self._finding(
            "CUSTOM_PRIVACY_RULE_VIOLATION",
            "medium",
            "Custom privacy rule is not satisfied",
            f"Rule {rule['id']} requires {missing_control} coverage for {field['name']}.",
            "Update the field metadata or revise the custom rule with an approved rationale.",
            evidence={"rule_id": rule["id"], "field": field["name"], "missing_control": missing_control},
        )

    def _likely_pii_categories(self, name):
        leaf = name.rsplit(".", 1)[-1].lower()
        return sorted(category for category, patterns in PII_NAME_PATTERNS.items() if any(
            pattern in leaf for pattern in patterns
        ))

    def _finding(self, code, severity, title, message, recommendation, *, evidence):
        return AssessmentFinding(
            code=code,
            severity=severity,
            title=title,
            message=message,
            framework_refs=["NIST Privacy Framework", "GDPR Art. 32", "PCI DSS v4.0", "SOC 2"],
            evidence=evidence,
            recommendation=recommendation,
            confidence=0.85 if code.startswith("PII_") else 1.0,
        )

    def _validate_collection(self, value, field, limit):
        if not isinstance(value, list):
            raise ValueError(f"{field} must be a list")
        if len(value) > limit:
            raise ValueError(f"{field} exceeds {limit} entries")

    def _require_object(self, value, field):
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{field} must be a non-empty object")

    def _require_text(self, value, field):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        self._validate_optional_text(value, field)

    def _validate_optional_text(self, value, field):
        if value is None:
            return
        if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH or any(
            ord(char) < 32 and char not in "\n\t" for char in value
        ):
            raise ValueError(f"{field} exceeds safe text limits")

    def _validate_optional_reference(self, value, field):
        if value is None:
            return
        if not isinstance(value, str) or not value or len(value) > MAX_REFERENCE_LENGTH or any(
            ord(char) < 32 for char in value
        ):
            raise ValueError(f"{field} must be a safe non-empty reference")

    def _validate_references(self, value, field):
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError(f"{field} must be a list of at most 100 references")
        for reference in value:
            self._validate_optional_reference(reference, f"{field} entry")


def register(registry):
    registry(DataPrivacyAssessmentAdapter.name, DataPrivacyAssessmentAdapter)
