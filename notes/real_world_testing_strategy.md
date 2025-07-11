# Real-World Testing Strategy for Clustrix

**Date:** July 11, 2025  
**Objective:** Convert mock-based tests to real-world tests that validate external dependencies

## Current State Analysis

### 🔍 Mock Usage Identified
- **42 test files** contain mock/patch usage
- **Heavy reliance on mocks** for:
  - Cloud provider APIs (AWS, Azure, GCP)
  - SSH connections and file operations
  - File I/O operations
  - Database operations  
  - Network requests
  - Widget rendering

### 📋 External Dependencies Found

#### **1. Cloud Provider APIs**
- **AWS**: boto3 (EC2, EKS, IAM, pricing APIs)
- **Azure**: azure-identity, azure-mgmt-* (compute, container, resource, network)
- **GCP**: google-cloud-compute, google-cloud-container, google-auth
- **Lambda Cloud**: Custom API via requests
- **HuggingFace**: huggingface_hub

#### **2. Network/SSH Operations**
- **Paramiko**: SSH connections, SFTP file transfers
- **Requests**: HTTP API calls

#### **3. File/Database Operations**
- **Local filesystem**: File creation, reading, writing
- **Remote filesystem**: SSH-based file operations
- **Configuration files**: YAML/JSON persistence

#### **4. Widget/UI Components**
- **Jupyter widgets**: Visual components requiring screenshot validation

## 🎯 Real-World Testing Strategy

### **Phase 1: Infrastructure & Setup**

#### **1.1 Test Environment Setup**
```bash
# Create real-world test environment
mkdir -p tests/real_world/
mkdir -p tests/real_world/credentials/
mkdir -p tests/real_world/temp_files/
mkdir -p tests/real_world/screenshots/
```

#### **1.2 Credential Management**
- Create `.env.test` file for API keys/credentials
- Use environment variables for sensitive data
- Implement credential validation before tests

#### **1.3 Cost Management**
- Implement usage limits for API calls
- Cache results where appropriate
- Use smallest/cheapest resources for testing

### **Phase 2: Component-Specific Strategies**

#### **2.1 Cloud Provider APIs**

**AWS Testing:**
```python
# Real AWS API calls with minimal cost
def test_aws_authentication_real():
    """Test real AWS authentication with STS get-caller-identity."""
    client = boto3.client('sts')
    response = client.get_caller_identity()
    assert 'Account' in response
    assert 'UserId' in response

def test_aws_pricing_real():
    """Test real AWS pricing API - free tier."""
    client = boto3.client('pricing', region_name='us-east-1')
    response = client.get_products(
        ServiceCode='AmazonEC2',
        Filters=[
            {'Type': 'TERM_MATCH', 'Field': 'instanceType', 'Value': 't2.micro'}
        ],
        MaxResults=1
    )
    assert len(response['PriceList']) > 0
```

**Azure Testing:**
```python
def test_azure_authentication_real():
    """Test real Azure authentication."""
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.resource import ResourceManagementClient
    
    credential = DefaultAzureCredential()
    # Use free tier - just list subscriptions
    client = ResourceManagementClient(credential, subscription_id)
    subscriptions = list(client.subscriptions.list())
    assert len(subscriptions) >= 0
```

**GCP Testing:**
```python
def test_gcp_authentication_real():
    """Test real GCP authentication."""
    from google.auth import default
    from google.cloud import compute_v1
    
    credentials, project = default()
    client = compute_v1.InstancesClient(credentials=credentials)
    # Use free tier - just list instances (returns empty if none)
    instances = client.list(project=project, zone="us-central1-a")
    assert instances is not None
```

#### **2.2 SSH Operations**

**Real SSH Testing:**
```python
def test_ssh_connection_real():
    """Test real SSH connection to localhost."""
    import paramiko
    
    # Test SSH to localhost (requires SSH server)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect('localhost', username=os.getenv('USER'))
        stdin, stdout, stderr = client.exec_command('echo "test"')
        result = stdout.read().decode().strip()
        assert result == "test"
    finally:
        client.close()

def test_sftp_file_operations_real():
    """Test real SFTP file operations."""
    import paramiko
    import tempfile
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test content")
        local_path = f.name
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect('localhost', username=os.getenv('USER'))
        
        sftp = client.open_sftp()
        remote_path = f"/tmp/clustrix_test_{uuid.uuid4().hex}"
        
        # Upload file
        sftp.put(local_path, remote_path)
        
        # Download and verify
        with tempfile.NamedTemporaryFile(mode='r', delete=False) as f:
            download_path = f.name
        
        sftp.get(remote_path, download_path)
        
        with open(download_path, 'r') as f:
            content = f.read()
        
        assert content == "test content"
        
        # Cleanup
        sftp.remove(remote_path)
        sftp.close()
        client.close()
    finally:
        os.unlink(local_path)
        os.unlink(download_path)
```

#### **2.3 File I/O Operations**

**Real File System Testing:**
```python
def test_file_operations_real():
    """Test real file operations with temporary files."""
    import tempfile
    import json
    import yaml
    
    test_data = {"key": "value", "number": 42}
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test JSON operations
        json_path = Path(tmpdir) / "test.json"
        with open(json_path, 'w') as f:
            json.dump(test_data, f)
        
        with open(json_path, 'r') as f:
            loaded_data = json.load(f)
        
        assert loaded_data == test_data
        
        # Test YAML operations
        yaml_path = Path(tmpdir) / "test.yaml"
        with open(yaml_path, 'w') as f:
            yaml.dump(test_data, f)
        
        with open(yaml_path, 'r') as f:
            loaded_data = yaml.safe_load(f)
        
        assert loaded_data == test_data
```

#### **2.4 Widget/UI Testing**

**Visual Verification Strategy:**
```python
def test_widget_visual_verification():
    """Test widget rendering with screenshot capture."""
    from clustrix import create_modern_cluster_widget
    from IPython.display import display
    import matplotlib.pyplot as plt
    
    # Create widget
    widget = create_modern_cluster_widget()
    
    # Render to image (requires headless browser setup)
    # This would need selenium or similar for real screenshot
    screenshot_path = "tests/real_world/screenshots/modern_widget.png"
    
    # For now, save widget HTML for manual inspection
    html_path = "tests/real_world/screenshots/modern_widget.html"
    with open(html_path, 'w') as f:
        f.write(widget._repr_html_())
    
    assert Path(html_path).exists()
    
    # TODO: Implement automated screenshot comparison
    # This requires headless browser setup
```

### **Phase 3: Integration Testing**

#### **3.1 End-to-End Workflows**
```python
def test_complete_cluster_workflow_real():
    """Test complete workflow with real cluster."""
    from clustrix import cluster
    
    @cluster(cores=1, memory="1GB", cluster_type="local")
    def simple_computation(x):
        return x * 2
    
    # Test with real execution
    result = simple_computation(5)
    assert result == 10
```

#### **3.2 Cross-Platform Testing**
```python
def test_cross_platform_compatibility():
    """Test platform-specific functionality."""
    import platform
    import clustrix
    
    # Test on current platform
    system = platform.system()
    
    if system == "Darwin":  # macOS
        # Test macOS-specific features
        pass
    elif system == "Linux":  # Linux
        # Test Linux-specific features
        pass
    elif system == "Windows":  # Windows
        # Test Windows-specific features
        pass
```

### **Phase 4: Cost-Conscious Implementation**

#### **4.1 API Call Limits**
```python
# Implement rate limiting and cost controls
class RealWorldTestManager:
    def __init__(self):
        self.api_calls_today = 0
        self.daily_limit = 100
        self.cost_limit_usd = 5.0
        self.current_cost = 0.0
    
    def can_make_api_call(self, estimated_cost=0.01):
        if self.api_calls_today >= self.daily_limit:
            return False
        if self.current_cost + estimated_cost > self.cost_limit_usd:
            return False
        return True
    
    def record_api_call(self, cost=0.01):
        self.api_calls_today += 1
        self.current_cost += cost
```

#### **4.2 Test Selection Strategy**
```python
# pytest markers for different test types
@pytest.mark.unit  # Fast, no external dependencies
@pytest.mark.integration  # Real external dependencies, low cost
@pytest.mark.expensive  # High cost, run manually
@pytest.mark.visual  # Requires manual verification
```

### **Phase 5: Implementation Plan**

#### **5.1 Priority Order**
1. **File I/O operations** (low cost, high impact)
2. **SSH operations** (medium cost, high impact)
3. **Free tier API calls** (medium cost, medium impact)
4. **Widget visual verification** (low cost, manual effort)
5. **Paid API calls** (high cost, run on-demand)

#### **5.2 Migration Strategy**
1. **Parallel implementation**: Keep existing mock tests
2. **Gradual replacement**: Replace mocks with real tests where feasible
3. **Hybrid approach**: Use real tests for validation, mocks for CI/CD speed
4. **Documentation**: Clear marking of which tests use real resources

## 🔧 Implementation Tools

### **Testing Framework Enhancements**
```python
# conftest.py additions
@pytest.fixture
def real_world_manager():
    """Manage real-world test resources."""
    return RealWorldTestManager()

@pytest.fixture
def temp_cluster_config():
    """Create temporary cluster configuration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test_config.yml"
        # Create minimal config for testing
        yield config_path

@pytest.fixture
def mock_credentials():
    """Provide test credentials from environment."""
    return {
        'aws_access_key': os.getenv('TEST_AWS_ACCESS_KEY'),
        'aws_secret_key': os.getenv('TEST_AWS_SECRET_KEY'),
        'azure_subscription_id': os.getenv('TEST_AZURE_SUBSCRIPTION_ID'),
        'gcp_project_id': os.getenv('TEST_GCP_PROJECT_ID'),
    }
```

## 📊 Success Metrics

### **Validation Criteria**
- ✅ **All external APIs validated** with real calls
- ✅ **File operations tested** with real filesystem
- ✅ **SSH connections verified** with real servers
- ✅ **Widgets visually confirmed** with screenshots
- ✅ **Cost limits respected** (< $10/month for all real tests)
- ✅ **Cross-platform compatibility** verified

### **Quality Gates**
- Real-world tests must pass before deployment
- Visual verification required for UI changes
- API cost monitoring in place
- Credential security validated

## 🚀 Next Steps

1. **Create test infrastructure** and credential management
2. **Implement file I/O real tests** (lowest risk)
3. **Add SSH real tests** with localhost
4. **Implement free-tier API tests**
5. **Create visual verification pipeline**
6. **Add cost monitoring and limits**
7. **Document testing procedures**

This strategy ensures that all Clustrix functionality is validated against real-world conditions while maintaining cost control and security.