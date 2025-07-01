# External Service Validation Plan

## 🎯 Objective
Systematically validate ALL external dependencies in Clustrix to ensure they work in practice, not just in theory.

## 📋 Resumable Validation Workflow

### Phase 1: Immediate Tests (No Credentials Required)
These can be tested right now:

1. **Azure Retail Prices API** ⏳
   - Public API, no authentication required
   - Test endpoint: `https://prices.azure.com/api/retail/prices`
   - Should work immediately

### Phase 2: Cloud Provider APIs (Require Setup)
2. **AWS Pricing API** 🔐
   - Requires AWS credentials
   - Need `pricing:GetProducts` permission
   - Can use AWS free tier

3. **GCP Cloud Billing Catalog API** 🔐  
   - Requires GCP service account
   - Need billing account setup
   - Can use GCP free tier

### Phase 3: SSH & Cluster Access (Require External Resources)
4. **SSH Key Generation/Deployment** 🔐
   - Need target server for testing
   - Could use local VM or cloud instance

5. **SLURM/PBS/SGE Integration** 🔐
   - Need access to academic/research clusters
   - May require institutional access

### Phase 4: Container & K8s (Require Setup)
6. **Docker Integration** 🔐
   - Need Docker daemon access
   - Test image building and registry push

7. **Kubernetes Integration** 🔐
   - Need K8s cluster (minikube, kind, or cloud)
   - Test job submission and monitoring

## 🔄 Validation Session Template

Each validation session should follow this pattern:

### Session Start Checklist:
- [ ] Update GitHub issue with current focus
- [ ] Create/update validation notes file
- [ ] Set up test environment
- [ ] Gather required credentials

### During Validation:
- [ ] Write minimal test script for the service
- [ ] Test happy path scenarios  
- [ ] Test error conditions
- [ ] Document any surprises or differences from expected behavior
- [ ] Note required permissions, setup steps, etc.

### Session End Checklist:
- [ ] Update GitHub issue with results
- [ ] Commit any test scripts or documentation updates
- [ ] Mark service as ✅ VALIDATED or ❌ FAILED in tracker
- [ ] Document next steps needed

## 📝 Documentation Requirements

For each validated service, document:

1. **Setup Instructions**:
   - Required credentials/accounts
   - Minimum permissions needed
   - Environment setup steps

2. **Working Examples**:
   - Minimal code that successfully calls the service
   - Sample inputs and expected outputs
   - Error handling examples

3. **Gotchas & Quirks**:
   - Undocumented API behavior
   - Regional differences
   - Rate limiting or quota issues
   - Authentication edge cases

## 🏃‍♂️ Quick Start: First Validation

Let's start with Azure Retail Prices API since it requires no setup:

```bash
# Test script template
python -c "
import requests
response = requests.get('https://prices.azure.com/api/retail/prices', 
                       params={'api-version': '2021-10-01-preview'})
print(f'Status: {response.status_code}')
print(f'Response: {response.json()[:100] if response.status_code == 200 else response.text}')
"
```

## 📊 Progress Tracking

Use GitHub issue #63 as the central tracker. After each validation session:

1. Update the status table in the issue
2. Add a timestamped comment with findings
3. Link to any new issues or documentation created
4. Identify the next priority for validation

## 🚀 Recovery Strategy

When context runs out or sessions end:

1. **Check GitHub Issue #63** for current status
2. **Read latest validation notes** in `notes/` directory  
3. **Continue from where "Currently Working On" indicates**
4. **Update issue immediately** when resuming work

This ensures no progress is lost and validation can be systematically completed over multiple sessions.