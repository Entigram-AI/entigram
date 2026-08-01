# Communication Drafter Checklist & Guideline

## Objective
Draft clear, accurate, and audience-tailored incident communication updates for stakeholders, developers, and status reports, including an incident timeline, impact summary, and next steps.

## Communication Drafting Checklist
- [ ] **Incident Timeline Construction**:
  - Establish chronology of events: Detection Time, Initial Triage, RCA Identification, Fix Plan/Rollback Decision, Target Resolution.
- [ ] **Technical Impact Summary**:
  - Describe what broken capabilities or degraded performance users/systems are experiencing.
- [ ] **Audience-Specific Communication Drafts**:
  1. **Engineering / Developer Briefing**: Highly technical, including file paths, stack trace excerpts, root cause mechanism, and PR/patch target.
  2. **Stakeholder / Executive Summary**: Non-technical high-level summary, operational impact, estimated time to recovery (ETR), and mitigation status.
  3. **Status Page / User Notice**: Clean, transparent public message detailing incident state (Investigating, Identified, Monitoring, Resolved).
- [ ] **Actionable Next Steps & Post-Mortem Inputs**:
  - Immediate mitigation steps, short-term verification tasks, and post-incident review (PIR) recommendations.

## Output Templates

### 1. Developer Briefing Template
```markdown
### 🔧 Engineering Triage Briefing
- **Severity**: P0 / P1 / P2
- **Failing Module**: `path/to/module.py:line`
- **Root Cause**: [Technical description of failure mechanism]
- **Current Status**: [Fix Proposed / Rollback Evaluated / Investigating]
- **Mitigation Path**: [Summary of selected fix or rollback]
- **Verification**: `pytest tests/unit/...`
```

### 2. Executive / Stakeholder Summary Template
```markdown
### 📊 Executive Incident Briefing
- **Incident Summary**: [Brief non-technical description of the issue]
- **Business Impact**: [Impact on operations, data processing, or user experience]
- **Current Status**: [Active triage / Fix underway / Resolved]
- **Estimated Resolution Time (ETR)**: [Timeframe or TBD]
- **Next Update Expected**: [Timeframe]
```

### 3. Public Status Notice Template
```markdown
### 📢 Status Notice: [Incident Title]
**Status**: [Investigating | Identified | Monitoring | Resolved]
**Impact**: [Description of service degradation]
**Update**: [Concise summary of progress and current operational posture]
```
