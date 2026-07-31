import unittest
import json
import sqlite3
from pathlib import Path
from entigram.schema_compiler.compiler import SchemaCompiler
from entigram.schema_compiler.parser import SchemaParser

class TestHealthLogic(unittest.TestCase):
    def setUp(self):
        self.workspace_root = Path(__file__).parent.parent
        self.test_dir = Path("test_workspace_health")
        if self.test_dir.exists():
            import shutil
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir()
        
        self.ehr_schema = self.test_dir / "EHRExtraction_schema.lds"
        self.cv_schema = self.test_dir / "ClinicalValidation_schema.lds"
        
        # Use singular entity names so compiler pluralizes them to expected table names
        self.ehr_schema.write_text("""
        ENTITY ehr_source_file { id UUID PK }
        ENTITY staged_patient_record { id UUID PK }
        ENTITY staged_observation { id UUID PK }
        """)
        
        self.cv_schema.write_text("""
        ENTITY clinical_mapping { id UUID PK }
        ENTITY validated_encounter { id UUID PK }
        """)

    def tearDown(self):
        if self.test_dir.exists():
            import shutil
            shutil.rmtree(self.test_dir)

    def test_ehr_extraction_schema_compilation(self):
        """Verifies that the EHR Extraction Schema compiles to valid SQL."""
        parser = SchemaParser(self.ehr_schema.read_text())
        entities, rels = parser.parse()
        compiler = SchemaCompiler(entities, rels)
        sql = compiler.compile()
        
        self.assertIn("CREATE TABLE IF NOT EXISTS ehr_source_files", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS staged_patient_records", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS staged_observations", sql)

    def test_clinical_validation_schema_compilation(self):
        """Verifies that the Clinical Validation Schema compiles to valid SQL."""
        parser = SchemaParser(self.cv_schema.read_text())
        entities, rels = parser.parse()
        compiler = SchemaCompiler(entities, rels)
        sql = compiler.compile()
        
        self.assertIn("CREATE TABLE IF NOT EXISTS clinical_mappings", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS validated_encounters", sql)

    def test_cross_domain_state_sensing(self):
        """
        Simulates a cross-domain sensing scenario where EHRExtraction and ClinicalValidation
        might have conflicting views of a medical observation.
        """
        # Source State (EHR Agent)
        ehr_state = {
            "observation_type": "Glucose",
            "observation_value": "120"
        }
        
        # Target State (Validation Agent)
        cv_state = {
            "mapped_concept": "Glucose",
            "status": "Validated",
            "observation_value": "118" # Conflict!
        }
        
        # We simulate the Broker's detection logic
        from entigram.broker import EntigramBroker
        broker = EntigramBroker(str(self.workspace_root))
        
        # Manually authorize an alignment in the ledger for this test
        broker.authorize_alignment(
            source_domain="EHRExtraction",
            target_domain="ClinicalValidation",
            source_concept="observation_value",
            target_concept="observation_value",
            confidence=1.0,
            rationale="Direct mapping of raw value to validated value.",
            validate_schema=False,
        )
        
        conflicts = broker.detect_cross_domain_conflict(
            "EHRExtraction", "ClinicalValidation", ehr_state, cv_state
        )
        
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['entity_type'], "observation_value")
        self.assertEqual(conflicts[0]['proposed_states']['EHRExtraction'], "120")
        self.assertEqual(conflicts[0]['proposed_states']['ClinicalValidation'], "118")

if __name__ == "__main__":
    unittest.main()
