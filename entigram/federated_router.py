import re
import sqlite3
import threading
import json
import difflib
import inflect
from contextlib import closing
from pathlib import Path
from typing import List, Dict, Any, Optional
from .broker import EntigramBroker
from .graphql_bridge import GraphQLBridge
from .schema_compiler.parser import SchemaParser

p = inflect.engine()

class FederatedRouter:
    """
    Routes GraphQL-LD queries to the appropriate federated domain databases.
    Hardened with CozoDB (Datalog) for high-performance cross-domain joins.
    """
    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.broker = EntigramBroker(str(self.target_dir))
        self.etg_dir = self.target_dir / ".etg"

        # Lazy-initialised CozoDB client (has a native binary dependency)
        self._cozo = None

        self._synced_entities: set = set()
        self._sync_lock = threading.Lock()   # guards check-then-add on _synced_entities

        # Per-query caches — cleared only on new FederatedRouter instance
        self._domain_cache: Dict[str, str] = {}       # entity_name -> domain
        self._schema_cache: Dict[str, List[str]] = {} # entity_name -> [col_names]
        self._join_cache: Dict[tuple, Dict[str, str]] = {}  # (parent, child) -> join_info
        self._requires_recursive_sql = False

        # Load schema to understand which entity belongs where
        self.entities = {}
        self._load_schema()

    def close(self):
        self.broker.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    _COZO_UNAVAILABLE = object()  # sentinel value meaning "tried and failed"

    @property
    def cozo(self):
        if self._cozo is None:
            try:
                import pycozo
                self._cozo = pycozo.Client()
            except (ImportError, ModuleNotFoundError, Exception):
                self._cozo = FederatedRouter._COZO_UNAVAILABLE
        if self._cozo is FederatedRouter._COZO_UNAVAILABLE:
            return None
        return self._cozo

    def _load_schema(self):
        # Load global schema if it exists
        schema_path = self.target_dir / "schema.lds"
        if schema_path.exists():
            parser = SchemaParser(schema_path.read_text())
            entities, _ = parser.parse()
            self.entities.update(entities)
        
        # Installed packages live under .etg/packages. Retain the root packages
        # directory as a compatibility path for older workspaces.
        for packages_dir in (self.target_dir / ".etg" / "packages", self.target_dir / "packages"):
            if not packages_dir.exists():
                continue
            for pkg_schema in packages_dir.rglob("schema.lds"):
                try:
                    parser = SchemaParser(pkg_schema.read_text())
                    entities, _ = parser.parse()
                    self.entities.update(entities)
                except Exception as e:
                    print(f"Warning: Failed to load schema from {pkg_schema}: {e}")

    def _get_table_name(self, entity_name: str) -> str:
        return p.plural(entity_name.lower())

    def _find_domain_for_entity(self, entity_name: str) -> Optional[str]:
        if entity_name in self._domain_cache:
            return self._domain_cache[entity_name]

        table_name = self._get_table_name(entity_name)
        active_packages = self.broker.get_active_packages()

        for pkg in active_packages:
            db_path = self.etg_dir / "states" / f"{pkg}.db"
            if db_path.exists():
                with closing(sqlite3.connect(db_path)) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table_name,)
                    )
                    if cursor.fetchone():
                        self._domain_cache[entity_name] = pkg
                        return pkg
        return None

    def _get_entity_columns(self, entity_name: str) -> List[str]:
        """Returns column names for entity, cached after first fetch."""
        if entity_name in self._schema_cache:
            return self._schema_cache[entity_name]

        domain = self._find_domain_for_entity(entity_name)
        if not domain:
            return []
        db_path = self.etg_dir / "states" / f"{domain}.db"
        with closing(sqlite3.connect(db_path)) as conn:
            cols = [c[1] for c in conn.execute(
                f"PRAGMA table_info({self._get_table_name(entity_name)})"
            ).fetchall()]
        self._schema_cache[entity_name] = cols
        return cols

    def execute(self, graphql_query: str) -> List[Dict[str, Any]]:
        """
        Executes a GraphQL query by translating it to Datalog and running it in CozoDB.
        Ensures index-free adjacency for high-performance cross-domain joins.
        """
        tokens = re.findall(r'\w+|[{}()]|[:,]', graphql_query)
        if not tokens: return []
        
        involved_entities = self._extract_involved_entities(tokens.copy())

        for ent in involved_entities:
            with self._sync_lock:
                if ent not in self._synced_entities:
                    if not self._sync_entity_to_cozo(ent):
                        # If sync fails, force fallback to SQL for this query
                        print(f"Info: CozoDB sync unavailable for '{ent}', falling back to SQL.")
                        fallback_tokens = re.findall(r'\w+|[{}()]|[:,]', graphql_query)
                        return self._execute_recursive(fallback_tokens)
                    self._synced_entities.add(ent)

        # Translate GraphQL to Datalog with Projection Map
        self._requires_recursive_sql = False
        datalog_query, projection_map, options = self._translate_to_datalog(graphql_query)
        if self._requires_recursive_sql:
            fallback_tokens = re.findall(r'\w+|[{}()]|[:,]', graphql_query)
            return self._execute_recursive(fallback_tokens)

        try:
            if self.cozo is None:
                raise ImportError("CozoDB (pycozo) is not installed or available.")
            res = self.cozo.run(datalog_query)
            flat_results = res.to_dict('records')

            if options.get('offset'):
                flat_results = flat_results[int(options['offset']):]
            if options.get('limit'):
                flat_results = flat_results[:int(options['limit'])]

            return self._nest_results(flat_results, projection_map)
        except Exception as e:
            print(f"Warning: CozoDB execution failed ({type(e).__name__}: {e}), falling back to recursive SQL.")
            # Re-tokenize so fallback gets a clean token stream (not the consumed one above)
            fallback_tokens = re.findall(r'\w+|[{}()]|[:,]', graphql_query)
            return self._execute_recursive(fallback_tokens)

    def _extract_involved_entities(self, tokens: List[str]) -> List[str]:
        entities = []
        while tokens:
            t = tokens.pop(0)
            if t == "{": continue
            if t == "}": continue
            # If it's a word and followed by {, it's likely an entity
            if tokens and tokens[0] == "{":
                entities.append(t)
        return list(set(entities))

    def _sync_entity_to_cozo(self, entity_name: str) -> bool:
        if self.cozo is None:
            return False

        domain = self._find_domain_for_entity(entity_name)
        if not domain:
            return False

        table_name = self._get_table_name(entity_name)
        db_path = self.etg_dir / "states" / f"{domain}.db"
        rel_name = entity_name.lower()

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(f"PRAGMA table_info({table_name})")
            cols = cursor.fetchall()
            col_names = [c[1] for c in cols]

            if not col_names:
                return False

            pk_col = col_names[0]
            for c in cols:
                if c[5]:
                    pk_col = c[1]

            col_defs = [f"{c}: Any" for c in col_names if c != pk_col]
            create_stmt = f":create {rel_name} {{ {pk_col}: Any => {', '.join(col_defs)} }}"
            try:
                self.cozo.run(create_stmt)
            except Exception as e:
                err_msg = str(e).lower()
                if "already" not in err_msg and "exists" not in err_msg:
                    print(f"Warning: Failed to create CozoDB relation '{rel_name}': {e}")

            cursor.execute(f"SELECT * FROM {table_name}")
            rows = [dict(r) for r in cursor.fetchall()]
            
            if not rows:
                return True # Empty but synced

            # Filter rows to only include columns defined in col_names
            # This prevents errors if sqlite3 returns hidden columns or if schema changed
            filtered_rows = []
            for row in rows:
                filtered_rows.append({k: v for k, v in row.items() if k in col_names})

            # Insert data
            try:
                self.cozo.insert(rel_name, filtered_rows)
                return True
            except Exception as e:
                print(f"Warning: Failed to insert data into CozoDB '{rel_name}': {e}")
                return False

    def _nest_results(self, flat_results: List[Dict[str, Any]], projection_map: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        """
        Transforms flat Cozo results into nested GraphQL structure.
        """
        nested_results = []
        for row in flat_results:
            nested_obj = {}
            for cozo_var, path in projection_map.items():
                if cozo_var in row:
                    val = row[cozo_var]
                    # Traverse path to place the value
                    curr = nested_obj
                    for i, segment in enumerate(path[:-1]):
                        if segment not in curr:
                            curr[segment] = {}
                        curr = curr[segment]
                    curr[path[-1]] = val
            nested_results.append(nested_obj)
        return nested_results

    def _translate_to_datalog(self, graphql_query: str):
        tokens = re.findall(r'\w+|[{}()]|[:,]', graphql_query)
        if not tokens:
            return "?[] := ", {}, {}
        if tokens[0] == "query":
            tokens.pop(0)

        options = {}
        # Simple extraction of top-level args
        # query { Entity(first: 10, offset: 5) { ... } }
        if len(tokens) > 2 and tokens[2] == "(":
            i = 3
            while i < len(tokens) and tokens[i] != ")":
                if tokens[i] in ("first", "limit"):
                    if i + 2 < len(tokens):
                        options['limit'] = tokens[i + 2]
                elif tokens[i] == "offset":
                    if i + 2 < len(tokens):
                        options['offset'] = tokens[i + 2]
                i += 1

        if not tokens:
            return "?[] := ", {}, {}
        if tokens[0] == "{":
            tokens.pop(0)

        rules = []
        output_vars = []
        projection_map = {} # cozo_var -> path
        
        def walk(current_tokens, parent_entity=None, parent_pk_var=None, current_path=None, parent_vars=None):
            if not current_tokens or current_tokens[0] == "}":
                return
            
            entity_name = current_tokens.pop(0)
            
            # Skip arguments if present
            if current_tokens and current_tokens[0] == "(":
                depth = 1
                while depth > 0 and current_tokens:
                    t = current_tokens.pop(0)
                    if t == "(": depth += 1
                    elif t == ")": depth -= 1
            
            rel_name = entity_name.lower()
            path = (current_path or [])
            
            domain = self._find_domain_for_entity(entity_name)
            if not domain:
                return

            cols = self._get_entity_columns(entity_name)
            
            this_vars = {c: f"{rel_name}_{c}_{len(rules)}" for c in cols} # Unique vars per instance
            rule = f"*{rel_name}[{', '.join([this_vars[c] for c in cols])}]"
            rules.append(rule)
            
            # Find PK
            pk_col = "id"
            if entity_name in self.entities:
                for attr in self.entities[entity_name].attributes:
                    if attr['pk']: pk_col = attr['name']
            
            this_pk_var = this_vars.get(pk_col)

            if parent_entity and parent_pk_var:
                join_info = self._resolve_join_info(parent_entity, entity_name)
                if join_info.get('direction') == 'no_join':
                    self._requires_recursive_sql = True
                elif join_info['direction'] == 'child_has_fk':
                    fk_var = this_vars.get(join_info['fk_col'])
                    parent_key_col = join_info.get('parent_key_col')
                    
                    # If alignment specified a non-PK parent column, we need the variable for that column
                    target_parent_var = parent_pk_var
                    if parent_key_col and parent_vars:
                        target_parent_var = parent_vars.get(parent_key_col, parent_pk_var)
                    
                    if fk_var:
                        rules.append(f"{fk_var} == {target_parent_var}")

            if current_tokens and current_tokens[0] == "{":
                current_tokens.pop(0) # {
                while current_tokens and current_tokens[0] != "}":
                    field = current_tokens[0]
                    if len(current_tokens) > 1 and current_tokens[1] == "{":
                        # It's a nested entity, keep path segment
                        walk(current_tokens, entity_name, this_pk_var, path + [field], this_vars)
                    else:
                        field = current_tokens.pop(0)
                        if field in this_vars:
                            out_var = this_vars[field]
                            output_vars.append(out_var)
                            projection_map[out_var] = path + [field]
                if current_tokens: current_tokens.pop(0) # }
        
        walk(tokens)

        if not output_vars or not rules:
            raise ValueError("No output variables resolved; cannot build valid Datalog query.")

        datalog = f"?[{', '.join(output_vars)}] := " + ",\n  ".join(rules)
        return datalog, projection_map, options

    def _execute_recursive(self, tokens: List[str], filter_col: Optional[str] = None, filter_val: Any = None) -> List[Dict[str, Any]]:
        # Skip a leading `{` that wraps the entire query
        if tokens and tokens[0] == "{":
            tokens.pop(0)

        if not tokens or tokens[0] == "}":
            return []

        root_entity = tokens.pop(0)
        domain = self._find_domain_for_entity(root_entity)
        
        if not domain:
            return []

        # 2. Extract fields and nested blocks
        if tokens and tokens[0] == "{": tokens.pop(0)
        
        fields = []
        nested_queries = {} # field_name -> tokens_block
        
        while tokens and tokens[0] != "}":
            field = tokens.pop(0)
            if tokens and tokens[0] == "{":
                # Nested block
                tokens.pop(0) # {
                # Find matching }
                nested_tokens = [field, "{"] # Start with field name and open brace for recursive call
                depth = 1
                while depth > 0 and tokens:
                    t = tokens.pop(0)
                    if t == "{": depth += 1
                    elif t == "}": depth -= 1
                    nested_tokens.append(t)
                nested_queries[field] = nested_tokens
            else:
                fields.append(field)
        
        if tokens: tokens.pop(0) # pop the closing } for the current level

        # 3. Generate SQL for current level
        table_name = self._get_table_name(root_entity)
        
        # We ALWAYS need the PK for joins, even if not requested
        pk_col = "id" # Default
        if root_entity in self.entities:
            for attr in self.entities[root_entity].attributes:
                if attr['pk']: pk_col = attr['name']
        
        query_fields = list(set(fields + [pk_col]))
        
        # PRE-SCAN nested queries to find any keys we need from THIS entity
        for field in nested_queries.keys():
            join_info = self._resolve_join_info(root_entity, field)
            if join_info['direction'] == 'parent_has_fk':
                query_fields.append(join_info['fk_col'])
            elif join_info['direction'] == 'child_has_fk':
                parent_key_col = join_info.get('parent_key_col')
                if parent_key_col:
                    query_fields.append(parent_key_col)
        
        # Validate query_fields against the actual column whitelist (SQL injection prevention)
        valid_cols = set(self._get_entity_columns(root_entity))
        query_fields = [f for f in set(query_fields) if f in valid_cols]
        
        if not query_fields:
            return []

        db_path = self.etg_dir / "states" / f"{domain}.db"
        params: List[Any] = []
        sql = f"SELECT {', '.join(query_fields)} FROM {table_name}"
        if filter_col and filter_val is not None:
            if filter_col not in valid_cols:
                return []
            sql += f" WHERE {filter_col} = ?"
            params.append(filter_val)

        # 4. Execute current level
        results = []
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            for row in cursor.fetchall():
                results.append(dict(row))
        except Exception as e:
            return []
        finally:
            if conn:
                conn.close()

        # 5. Handle nested queries — batch fetch to avoid N+1
        for field, nested_tokens_block in nested_queries.items():
            join_info = self._resolve_join_info(root_entity, field)
            if join_info.get('direction') == 'no_join':
                for res in results:
                    res[field] = None
                continue

            if join_info['direction'] == 'child_has_fk':
                # Batch: collect all parent keys and fetch children in one query
                fk_col = join_info['fk_col']
                parent_key_col = join_info.get('parent_key_col', pk_col)
                parent_ids = [r[parent_key_col] for r in results if parent_key_col in r]
                
                if not parent_ids:
                    for res in results:
                        res[field] = None
                    continue

                child_entity = nested_tokens_block[0]
                child_domain = self._find_domain_for_entity(child_entity)
                # Skip batch when the child block has further nesting — recurse instead
                has_sub_nesting = nested_tokens_block.count('{') > 0
                if child_domain and not has_sub_nesting:
                    child_table = self._get_table_name(child_entity)
                    child_db = self.etg_dir / "states" / f"{child_domain}.db"
                    child_valid_cols = set(self._get_entity_columns(child_entity))
                    if fk_col in child_valid_cols:
                        placeholders = ",".join("?" * len(parent_ids))
                        child_conn = None
                        try:
                            child_conn = sqlite3.connect(child_db)
                            child_conn.row_factory = sqlite3.Row
                            rows = child_conn.execute(
                                f"SELECT * FROM {child_table} WHERE {fk_col} IN ({placeholders})",
                                parent_ids
                            ).fetchall()
                        except Exception as e:
                            print(f"Batch join error for {field}: {e}")
                            rows = []
                        finally:
                            if child_conn:
                                child_conn.close()

                        by_fk: Dict[Any, List[Dict]] = {}
                        for row in rows:
                            d = dict(row)
                            by_fk.setdefault(d.get(fk_col), []).append(d)

                        for res in results:
                            children = by_fk.get(res.get(parent_key_col), [])
                            res[field] = children[0] if children else None
                        continue

                # Fallback to single-row recursive (handles sub-nesting and missing domain)
                for res in results:
                    nested_results = self._execute_recursive(list(nested_tokens_block), fk_col, res.get(parent_key_col))
                    res[field] = nested_results[0] if nested_results else None
            else:
                # Inverse: Parent has FK pointing to Child — fetch one at a time
                for res in results:
                    fk_col_name = join_info['fk_col']
                    fk_val = res.get(fk_col_name)
                    if fk_val:
                        nested_pk = "id"
                        if field in self.entities:
                            for a in self.entities[field].attributes:
                                if a['pk']:
                                    nested_pk = a['name']
                        
                        target_col = join_info.get('child_key_col', nested_pk)
                        nested_results = self._execute_recursive(list(nested_tokens_block), target_col, fk_val)
                        res[field] = nested_results[0] if nested_results else None
                    else:
                            res[field] = None

        return results

    def _resolve_join_info(self, parent_entity: str, child_entity: str) -> Dict[str, str]:
        """
        Resolves join column and direction.
        Returns {'fk_col': str, 'direction': 'child_has_fk' | 'parent_has_fk'}
        Result is cached per (parent, child) pair.
        """
        cache_key = (parent_entity, child_entity)
        if cache_key in self._join_cache:
            return self._join_cache[cache_key]

        result = self._resolve_join_info_uncached(parent_entity, child_entity)
        self._join_cache[cache_key] = result
        return result

    def _resolve_join_info_uncached(self, parent_entity: str, child_entity: str) -> Dict[str, str]:
        # 1. Check Semantic Alignments — scoped to the participating domains only
        parent_domain = self._find_domain_for_entity(parent_entity)
        child_domain = self._find_domain_for_entity(child_entity)
        alignments = self.broker.ledger.get_alignments(trusted_only=True)

        # print(f"[DEBUG] Resolving join between {parent_entity} ({parent_domain}) and {child_entity} ({child_domain})")

        candidates = []
        STRONG_KEYS = {"tax_id", "ein", "email", "ssn", "vat_id", "business_id", "ein_number", "tax_number"}

        for aln in alignments:
            # Restrict to alignments that involve both of these specific domains
            aln_domains = {aln['source_domain'], aln['target_domain']}
            if parent_domain and child_domain:
                if parent_domain not in aln_domains or child_domain not in aln_domains:
                    continue

            s_con, t_con = aln['source_concept'], aln['target_concept']
            
            res = None
            # Case A: Child has FK pointing to Parent (Standard)
            if s_con.startswith(f"{child_entity}.") and t_con.startswith(f"{parent_entity}."):
                res = {
                    'fk_col': s_con.split(".")[1], 
                    'parent_key_col': t_con.split(".")[1],
                    'direction': 'child_has_fk',
                    'confidence': aln['confidence']
                }
            elif t_con.startswith(f"{child_entity}.") and s_con.startswith(f"{parent_entity}."):
                res = {
                    'fk_col': t_con.split(".")[1], 
                    'parent_key_col': s_con.split(".")[1],
                    'direction': 'child_has_fk',
                    'confidence': aln['confidence']
                }
            # Case B: Parent has FK pointing to Child (Inverse)
            elif s_con.startswith(f"{parent_entity}.") and t_con.startswith(f"{child_entity}."):
                res = {
                    'fk_col': s_con.split(".")[1], 
                    'child_key_col': t_con.split(".")[1],
                    'direction': 'parent_has_fk',
                    'confidence': aln['confidence']
                }
            elif t_con.startswith(f"{parent_entity}.") and s_con.startswith(f"{child_entity}."):
                res = {
                    'fk_col': t_con.split(".")[1], 
                    'child_key_col': s_con.split(".")[1],
                    'direction': 'parent_has_fk',
                    'confidence': aln['confidence']
                }
            
            if res:
                # Heuristic: Boost strong semantic keys
                cols = [res.get('fk_col', ""), res.get('parent_key_col', ""), res.get('child_key_col', "")]
                if any(c.lower() in STRONG_KEYS for c in cols):
                    res['confidence'] += 0.1 # Slight boost to prefer semantic matches over IDs
                candidates.append(res)

        if candidates:
            # Sort by confidence descending
            candidates.sort(key=lambda x: x['confidence'], reverse=True)
            return candidates[0]

        # 2. Automated Join Discovery — check .etg/packages first, then root packages/
        if parent_domain and child_domain:
            src_schema = self.etg_dir / "packages" / child_domain / "schema.lds"
            if not src_schema.exists():
                src_schema = self.target_dir / "packages" / child_domain / "schema.lds"
            tgt_schema = self.etg_dir / "packages" / parent_domain / "schema.lds"
            if not tgt_schema.exists():
                tgt_schema = self.target_dir / "packages" / parent_domain / "schema.lds"

            if src_schema.exists() and tgt_schema.exists():
                proposals = self.broker.negotiate_alignments(str(src_schema), str(tgt_schema), threshold=0.7)
                proposals.sort(key=lambda x: x['confidence'], reverse=True)
                for p in proposals:
                    if p['source_concept'].startswith(f"{child_entity}.") and p['target_concept'].startswith(f"{parent_entity}."):
                        if p['confidence'] > 0.8:
                            self.broker.propose_alignment(
                                child_domain, parent_domain, p['source_concept'], p['target_concept'],
                                p['confidence'], f"Auto-Discovered structural join for {parent_entity}->{child_entity}",
                                source_artifact=f"{src_schema}::{tgt_schema}",
                            )
                            break

        # Closed-world cross-domain boundary: unverified discovery and naming
        # heuristics may create proposals, but they cannot drive operational joins.
        if parent_domain and child_domain and parent_domain != child_domain:
            parent_name_lower = parent_entity.lower()
            parent_domain_lower = (parent_domain or "").lower()
            for col in self._get_entity_columns(child_entity):
                col_lower = col.lower()
                if not (col_lower.endswith("_id") or col_lower.endswith("_ref") or col_lower.endswith("_fk")):
                    continue
                prefix = col_lower.rsplit("_", 1)[0]
                if (difflib.SequenceMatcher(None, prefix, parent_name_lower).ratio() >= 0.5 or
                        difflib.SequenceMatcher(None, prefix, parent_domain_lower).ratio() >= 0.5):
                    self.broker.propose_alignment(
                        child_domain,
                        parent_domain,
                        f"{child_entity}.{col}",
                        f"{parent_entity}.id",
                        0.7,
                        f"Heuristic cross-domain join candidate for {parent_entity}->{child_entity}",
                        source_artifact="router_column_scan",
                    )
                    break
            return {'direction': 'no_join'}

        # 3. Column scan — prefix-match _ref/_id/_fk columns against parent entity/domain name
        parent_name_lower = parent_entity.lower()
        parent_domain_lower = (parent_domain or "").lower()
        for col in self._get_entity_columns(child_entity):
            col_lower = col.lower()
            if not (col_lower.endswith("_id") or col_lower.endswith("_ref") or col_lower.endswith("_fk")):
                continue
            prefix = col_lower.rsplit("_", 1)[0]
            if (difflib.SequenceMatcher(None, prefix, parent_name_lower).ratio() >= 0.5 or
                    difflib.SequenceMatcher(None, prefix, parent_domain_lower).ratio() >= 0.5):
                return {'fk_col': col, 'direction': 'child_has_fk'}

        # 4. Fallback to naming convention
        pk_col = "id"
        if parent_entity in self.entities:
            for attr in self.entities[parent_entity].attributes:
                if attr['pk']: pk_col = attr['name']

        return {'fk_col': f"{parent_entity.lower()}_{pk_col}", 'direction': 'child_has_fk'}
