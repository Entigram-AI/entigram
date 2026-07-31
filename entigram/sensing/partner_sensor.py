import csv
import json
import sqlite3
import re
from pathlib import Path


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")


def _validated_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            f"{label} must start with a letter and contain only letters, numbers, and underscores"
        )
    return value


def _quoted_identifier(value: str) -> str:
    if "\x00" in value:
        raise ValueError("SQLite identifiers cannot contain NUL bytes")
    return '"' + value.replace('"', '""') + '"'


def _validated_headers(headers: list) -> list:
    if not all(isinstance(header, str) and header for header in headers):
        raise ValueError("all source fields must have non-empty string names")
    if len(headers) != len(set(headers)):
        raise ValueError("source field names must be unique")
    for header in headers:
        _quoted_identifier(header)
    return headers


class PartnerCSVSensor:
    """
    Ingests disparate partner data (CSV) and creates a localized domain database.
    This simulates an external partner providing data in their own proprietary format.
    """
    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.states_dir = self.target_dir / ".etg" / "states"
        self.states_dir.mkdir(parents=True, exist_ok=True)

    def ingest_csv(self, csv_path: str, domain_name: str, table_name: str):
        """
        Creates a SQLite database for the domain and populates it with CSV data.
        Infers types simplistically.
        """
        domain_name = _validated_identifier(domain_name, "domain name")
        table_name = _validated_identifier(table_name, "table name")
        db_path = (self.states_dir / f"{domain_name}.db").resolve()
        if self.states_dir != db_path.parent:
            raise ValueError("domain database path escapes the workspace states directory")
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            headers = _validated_headers(reader.fieldnames or [])
            rows = list(reader)

        if not headers:
            print(f"❌ Error: CSV {csv_path} has no headers.")
            return False

        return self._ingest_data(db_path, table_name, headers, rows)

    def _ingest_data(self, db_path: Path, table_name: str, headers: list, rows: list):
        conn = sqlite3.connect(db_path)
        try:
            # Drop table if exists for clean demo
            quoted_table = _quoted_identifier(_validated_identifier(table_name, "table name"))
            headers = _validated_headers(headers)
            conn.execute(f"DROP TABLE IF EXISTS {quoted_table}")
            
            # Create table
            cols = ", ".join([f'{_quoted_identifier(h)} TEXT' for h in headers])
            conn.execute(f"CREATE TABLE {quoted_table} ({cols})")
            
            # Insert data
            header_cols = ", ".join([_quoted_identifier(h) for h in headers])
            placeholders = ", ".join(["?" for _ in headers])
            sql = f"INSERT INTO {quoted_table} ({header_cols}) VALUES ({placeholders})"
            
            data = [tuple(str(row.get(h, "")) for h in headers) for row in rows]
            conn.executemany(sql, data)
            conn.commit()
            
            print(f"✅ Ingested {len(rows)} rows into {db_path.name}.{table_name}")
            return True
        except Exception as e:
            print(f"❌ Ingestion Error: {e}")
            return False
        finally:
            conn.close()

class PartnerJSONSensor:
    """
    Ingests disparate partner data (JSON) and creates a localized domain database.
    Supports list of objects.
    """
    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.states_dir = self.target_dir / ".etg" / "states"
        self.states_dir.mkdir(parents=True, exist_ok=True)

    def ingest_json(self, json_path: str, domain_name: str, table_name: str):
        domain_name = _validated_identifier(domain_name, "domain name")
        table_name = _validated_identifier(table_name, "table name")
        db_path = (self.states_dir / f"{domain_name}.db").resolve()
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if not isinstance(data, list) or not data or not all(isinstance(row, dict) for row in data):
            print(f"❌ Error: JSON {json_path} must be a non-empty list of objects.")
            return False
            
        headers = _validated_headers(list(data[0].keys()))
        
        # Use common ingestion logic
        csv_sensor = PartnerCSVSensor(str(self.target_dir))
        return csv_sensor._ingest_data(db_path, table_name, headers, data)

if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Entigram Partner Sensor")
    parser.add_argument("file", help="Path to CSV or JSON file")
    parser.add_argument("domain", help="Domain name")
    parser.add_argument("table", help="Table name")
    parser.add_argument("--dir", default=".", help="Target directory")
    
    args = parser.parse_args()
    
    if args.file.endswith(".csv"):
        sensor = PartnerCSVSensor(args.dir)
        sensor.ingest_csv(args.file, args.domain, args.table)
    elif args.file.endswith(".json"):
        sensor = PartnerJSONSensor(args.dir)
        sensor.ingest_json(args.file, args.domain, args.table)
    else:
        print("❌ Error: File must be .csv or .json")
