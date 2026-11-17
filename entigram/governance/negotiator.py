import difflib
from functools import lru_cache
from typing import List, Dict, Any, Tuple
from ..schema_compiler.parser import SchemaParser, SchemaEntity


_SYNONYMS = {
    "vendor": ["supplier", "provider", "partner"],
    "supplier": ["vendor", "provider", "partner"],
    "uid": ["id", "uuid", "identifier", "pk"],
    "id": ["uid", "uuid", "identifier", "pk", "ref"],
    "tax_id": ["ein", "vat_id", "business_id"],
    "ein": ["tax_id", "vat_id", "business_id"],
    "trust_score": ["rating", "reliability_score"],
    "rating": ["trust_score", "reliability_score"],
}

_SEMANTIC_CLASSES = [
    {"customer", "client", "user", "person", "individual", "subject"},
    {"provider", "supplier", "vendor", "partner"},
    {"facility", "site", "organization", "warehouse", "office"},
]


@lru_cache(maxsize=4096)
def _compute_similarity(a: str, b: str, ledger_path: str = None) -> float:
    """Cached fuzzy-similarity between two lowercased strings."""
    if a == b:
        return 1.0
    
    # 1. Check Ledger Synonyms (Persistent)
    if ledger_path:
        from ..sqlite_ledger.manager import LedgerManager
        ledger = LedgerManager(ledger_path)
        try:
            syns = ledger.get_synonyms(a)
            if b in syns:
                return 0.95
        finally:
            ledger.close()

    # 2. Check Static Synonyms (Fallback)
    if a in _SYNONYMS and b in _SYNONYMS[a]:
        return 0.95
    if b in _SYNONYMS and a in _SYNONYMS[b]:
        return 0.95
    
    # 3. Check Semantic Classes
    for s_class in _SEMANTIC_CLASSES:
        if a in s_class and b in s_class:
            return 0.85
            
    return difflib.SequenceMatcher(None, a, b).ratio()


class AlignmentNegotiator:
    """
    Automates semantic reconciliation between isolated domains.
    Uses fuzzy matching on entity and attribute names to propose alignments.
    """
    def __init__(self, threshold: float = 0.6, ledger_path: str = None):
        self.threshold = threshold
        self.synonyms = _SYNONYMS
        self.ledger_path = ledger_path

    def _get_similarity(self, a: str, b: str) -> float:
        return _compute_similarity(a.lower(), b.lower(), self.ledger_path)

    def negotiate(self, source_schema: str, target_schema: str) -> List[Dict[str, Any]]:
        """
        Proposes alignments between two Schema models using Semantic Blocking.
        """
        source_entities, _ = SchemaParser(source_schema).parse()
        target_entities, _ = SchemaParser(target_schema).parse()

        proposals = []

        # Phase 3 Improvement: Semantic Blocking
        source_blocks = self._generate_semantic_blocks(source_entities)
        target_blocks = self._generate_semantic_blocks(target_entities)

        # Compare entities within matching blocks
        for block_id, s_ent_names in source_blocks.items():
            if block_id not in target_blocks:
                continue
            
            t_ent_names = target_blocks[block_id]
            for s_name in s_ent_names:
                s_ent = source_entities[s_name]
                for t_name in t_ent_names:
                    t_ent = target_entities[t_name]
                    
                    # 1. Compare Entity Names
                    name_sim = self._get_similarity(s_name, t_name)
                    
                    # 2. Compare Attribute Overlap
                    attr_sim = self._calculate_attribute_similarity(s_ent, t_ent)
                    
                    # Combined Confidence (weighted)
                    # Phase 3 Adjustment: Favor name similarity for broader semantic capture
                    confidence = (name_sim * 0.7) + (attr_sim * 0.3)
                    
                    if confidence >= self.threshold:
                        proposals.append({
                            "source_concept": s_name,
                            "target_concept": t_name,
                            "mapping_type": "skos:exactMatch" if confidence > 0.9 else "skos:closeMatch",
                            "confidence": round(confidence, 2),
                            "rationale": f"Block Match [{block_id}]: Name sim={name_sim:.2f}, Attr overlap={attr_sim:.2f}"
                        })

                    # Only deep-dive into properties when entities are somewhat similar OR
                    # the caller explicitly set a low threshold (broad discovery mode).
                    if name_sim >= 0.3 or attr_sim >= 0.4 or self.threshold <= 0.2:
                        prop_alignments = self._negotiate_properties(s_name, s_ent, t_name, t_ent)
                        proposals.extend(prop_alignments)

        return proposals

    def _generate_semantic_blocks(self, entities: Dict[str, SchemaEntity]) -> Dict[str, List[str]]:
        """
        Groups entities into blocks based on semantic hints.
        Blocks are non-exclusive (an entity can be in multiple blocks).
        """
        blocks = {}
        
        # Standard block categories
        categories = {
            "identity": ["user", "person", "account", "profile", "identity", "partner", "vendor", "supplier", "provider", "customer", "client"],
            "transaction": ["order", "invoice", "payment", "transaction", "receipt", "transfer"],
            "product": ["item", "product", "sku", "inventory", "part", "service"],
            "location": ["address", "site", "location", "facility", "warehouse", "office"],
        }

        for name, ent in entities.items():
            name_lower = name.lower()
            # Also check attribute names for keywords
            attr_names = [a['name'].lower() for a in ent.attributes]
            
            assigned = False
            
            for cat_id, keywords in categories.items():
                if any(k in name_lower for k in keywords) or \
                   any(k in attr_name for k in keywords for attr_name in attr_names):
                    blocks.setdefault(cat_id, []).append(name)
                    assigned = True
            
            # Fallback block: prefix-based (e.g., first 3 letters)
            if not assigned:
                prefix = name_lower[:3]
                blocks.setdefault(f"prefix_{prefix}", []).append(name)

            # In broad-discovery mode (very low threshold), every entity also joins an
            # all-pairs block so cross-category FK patterns can still be found.
            if self.threshold <= 0.2:
                blocks.setdefault("_all", []).append(name)

        return blocks

    def _calculate_attribute_similarity(self, s_ent: SchemaEntity, t_ent: SchemaEntity) -> float:
        s_attrs = {a['name'].lower() for a in s_ent.attributes}
        t_attrs = {a['name'].lower() for a in t_ent.attributes}
        
        if not s_attrs or not t_attrs:
            return 0.0
            
        # Count matches including synonyms
        matches = 0
        for s in s_attrs:
            for t in t_attrs:
                if self._get_similarity(s, t) > 0.8:
                    matches += 1
                    break
        
        union_size = len(s_attrs) + len(t_attrs) - matches
        return matches / union_size if union_size > 0 else 0.0

    def _negotiate_properties(self, s_entity_name: str, s_ent: SchemaEntity, t_entity_name: str, t_ent: SchemaEntity) -> List[Dict[str, Any]]:
        prop_proposals = []
        
        # Calculate entity similarity once
        ent_sim = self._get_similarity(s_entity_name, t_entity_name)

        for s_attr in s_ent.attributes:
            s_attr_name = s_attr['name'].lower()
            for t_attr in t_ent.attributes:
                t_attr_name = t_attr['name'].lower()
                
                confidence = 0.0
                rationale = ""

                # 1. Exact or fuzzy name match
                sim = self._get_similarity(s_attr_name, t_attr_name)
                
                # 2. Check for FK patterns
                is_s_fk = s_attr_name.endswith("_id") or s_attr_name.endswith("_ref") or s_attr_name == s_entity_name.lower() + "id"
                is_t_fk = t_attr_name.endswith("_id") or t_attr_name.endswith("_ref") or t_attr_name == t_entity_name.lower() + "id"
                
                sim_id_t = self._get_similarity(t_attr_name, "id")

                # Case A: s is an FK pointing to t's PK
                if is_s_fk and sim_id_t > 0.8:
                    # Check if the prefix of s matches t_entity_name
                    prefix = s_attr_name.split("_")[0]
                    sim_prefix = self._get_similarity(prefix, t_entity_name)
                    if sim_prefix >= 0.6:
                        confidence = 0.9
                        rationale = f"FK pattern match: {s_attr_name} appears to point to {t_entity_name}.id"

                # Case B: Generic synonym match (boosted if entities match)
                elif sim >= 0.8:
                    confidence = (sim * 0.7) + (ent_sim * 0.3)
                    rationale = f"Property match: {sim:.2f} (Entity sim: {ent_sim:.2f})"

                if confidence >= self.threshold:
                    prop_proposals.append({
                        "source_concept": f"{s_entity_name}.{s_attr['name']}",
                        "target_concept": f"{t_entity_name}.{t_attr['name']}",
                        "mapping_type": "skos:exactMatch" if confidence > 0.9 else "skos:closeMatch",
                        "confidence": round(confidence, 2),
                        "rationale": rationale
                    })
        return prop_proposals
