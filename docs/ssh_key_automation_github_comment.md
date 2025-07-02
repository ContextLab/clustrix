# SSH Key Automation Technical Design - Summary for Issue #57

## Overview
This design addresses the automated setup of SSH keys for passwordless cluster authentication, replacing the current manual process documented at https://clustrix.readthedocs.io/en/latest/ssh_setup.html.

## Core User Workflow
1. User provides password **once** in widget/CLI
2. Clicks "Setup SSH Keys" button
3. Clustrix automatically:
   - Generates SSH key pair (if needed)
   - Connects using password
   - Deploys public key to `~/.ssh/authorized_keys`
   - Tests passwordless connection
4. All future connections use SSH key (no passwords)

## Key Technical Components

### 1. Main Entry Point
```python
def setup_ssh_keys(config: ClusterConfig, password: str) -> Dict[str, Any]:
    """Returns: {success, key_path, error, details}"""
```

### 2. Process Flow
```
Check existing keys → Generate if needed → Deploy using password → Verify → Update config
```

### 3. Critical Fixes Needed
- **Proper error detection**: Current implementation reports success even when key auth fails
- **University cluster support**: Handle non-standard home directories, module requirements
- **Robust deployment**: Handle existing authorized_keys, permissions, disk quotas
- **Real verification**: Actually test passwordless auth, not just connection

## Implementation Plan

### Phase 1: Fix Core Issues (Priority)
- Fix deployment verification (currently broken)
- Add proper error handling and rollback
- Support Dartmouth cluster paths (`/dartfs-hpc/rc/home/b/{username}/`)
- **Test immediately on real clusters** (ndoli, tensor01)

### Phase 2: Robustness & Key Rotation
- Handle edge cases (quota, permissions, existing keys)
- Add progress feedback
- Implement retry logic
- **Add key rotation feature** (force refresh, age-based recommendations)

### Phase 3: Polish
- Improve widget UX
- Add CLI command
- Comprehensive testing

## Testing Strategy
- Test on SLURM cluster (ndoli) and SSH cluster (tensor01)
- Use 1Password for initial passwords during testing
- Verify passwordless access works end-to-end
- Test with actual clustrix job submissions
- Validate key rotation functionality

## Success Criteria
- Works reliably on test systems (ndoli, tensor01)
- No password prompts after initial setup
- Clear error messages when setup fails
- Supports key rotation for refreshing credentials
- Handles multi-user scenarios properly

Full technical design document available at: `/docs/ssh_key_automation_technical_design.md`