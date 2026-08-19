"""
Test the complete SLURM script workflow manually.
"""

import pytest
import paramiko
from tests.real_world import credentials


@pytest.mark.real_world
def test_full_manual_workflow():
    """Test the complete SLURM script workflow manually."""
    ndoli_creds = credentials.get_ndoli_credentials()
    if not ndoli_creds:
        pytest.skip("No ndoli credentials available")

    # Connect via SSH
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh_client.connect(
        hostname=ndoli_creds["host"],
        username=ndoli_creds["username"],
        password=ndoli_creds.get("password"),
        port=22,
    )

    print("Testing complete SLURM workflow manually...")

    # Get the most recent job directory
    stdin, stdout, stderr = ssh_client.exec_command(
        "ls -1td /tmp/clustrix_slurm_working/job_* 2>/dev/null | head -1"
    )
    latest_job_dir = stdout.read().decode().strip()

    if latest_job_dir:
        print(f"Testing workflow in: {latest_job_dir}")

        # Step 1: Run VENV1 deserialization
        print("\n=== STEP 1: VENV1 Deserialization ===")
        step1_script = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
python -c "
import pickle
import sys
import traceback

try:
    import dill
except ImportError:
    dill = None
try:
    import cloudpickle
except ImportError:
    cloudpickle = None

print('VENV1 - Deserializing function data')
print('Python version:', sys.version)

try:
    with open('function_data.pkl', 'rb') as f:
        data = pickle.load(f)
    
    # Try to deserialize function
    func = None
    try:
        func = dill.loads(data['function']) if dill else None
        print('Successfully deserialized function with dill')
    except Exception as e:
        print('Dill deserialization failed:', str(e))
        try:
            func = cloudpickle.loads(data['function']) if cloudpickle else None
            print('Successfully deserialized function with cloudpickle')
        except Exception as e2:
            print('Cloudpickle deserialization failed:', str(e2))
            # Try source code fallback
            func_info = data.get('func_info', {{}})
            if func_info.get('source'):
                print('Using source code fallback')
                # Remove @cluster decorator from source
                import textwrap
                source = func_info['source']
                lines = source.split('\n')
                clean_lines = []
                for line in lines:
                    if not line.strip().startswith('@'):
                        clean_lines.append(line)
                clean_source = '\n'.join(clean_lines)
                clean_source = textwrap.dedent(clean_source)
                
                # Create function from source
                namespace = {{}}
                exec(clean_source, namespace)
                func = namespace[func_info['name']]
                print('Successfully created function from source code')
            else:
                raise Exception('All deserialization methods failed')
    
    args = pickle.loads(data['args'])
    kwargs = pickle.loads(data['kwargs'])
    
    # Pass data to VENV2 for execution
    with open('function_deserialized.pkl', 'wb') as f:
        if 'clean_source' in locals():
            # Function was created from source code, pass the source
            pickle.dump({{'source': clean_source, 'func_name': func_info['name'], 'args': args, 'kwargs': kwargs}}, f, protocol=4)
        else:
            # Function was deserialized from binary, pass the function object
            pickle.dump({{'func': func, 'args': args, 'kwargs': kwargs}}, f, protocol=4)
    
    print('VENV1 - Function data prepared for VENV2')
    
except Exception as e:
    print('VENV1 - Error during deserialization:', str(e))
    traceback.print_exc()
    with open('error.pkl', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f, protocol=4)
    raise
"
"""

        stdin, stdout, stderr = ssh_client.exec_command(step1_script)
        step1_output = stdout.read().decode().strip()
        step1_error = stderr.read().decode().strip()
        print(f"STEP 1 OUTPUT:\n{step1_output}")
        if step1_error:
            print(f"STEP 1 ERROR:\n{step1_error}")

        # Check if function_deserialized.pkl was created
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/function_deserialized.pkl'"
        )
        file_check = stdout.read().decode().strip()
        print(f"Function deserialized file: {file_check}")

        # Step 2: Run VENV2 execution
        print("\n=== STEP 2: VENV2 Execution ===")
        step2_script = f"""
cd '{latest_job_dir}'
source venv2_execution/bin/activate
python -c "
import pickle
import sys
import traceback

print('VENV2 - Executing function')
print('Python version:', sys.version)

try:
    with open('function_deserialized.pkl', 'rb') as f:
        exec_data = pickle.load(f)
    
    if 'func' in exec_data:
        # Function object was passed
        func = exec_data['func']
    elif 'source' in exec_data:
        # Source code was passed, recreate function
        print('Recreating function from source code in VENV2')
        namespace = {{}}
        exec(exec_data['source'], namespace)
        func = namespace[exec_data['func_name']]
    else:
        raise Exception('No function or source code found')
    
    args = exec_data['args']
    kwargs = exec_data['kwargs']
    
    # Execute the function
    print('Executing function with args:', args, 'kwargs:', kwargs)
    result = func(*args, **kwargs)
    print('Function execution result:', result)
    
    with open('result_venv2.pkl', 'wb') as f:
        pickle.dump(result, f, protocol=4)
        
    print('VENV2 - Function execution completed')
    
except Exception as e:
    print('VENV2 - Error during execution:', str(e))
    traceback.print_exc()
    with open('error.pkl', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f, protocol=4)
    raise
"
"""

        stdin, stdout, stderr = ssh_client.exec_command(step2_script)
        step2_output = stdout.read().decode().strip()
        step2_error = stderr.read().decode().strip()
        print(f"STEP 2 OUTPUT:\n{step2_output}")
        if step2_error:
            print(f"STEP 2 ERROR:\n{step2_error}")

        # Check if result_venv2.pkl was created
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/result_venv2.pkl'"
        )
        result_check = stdout.read().decode().strip()
        print(f"Result VENV2 file: {result_check}")

        # Step 3: Run VENV1 result serialization
        print("\n=== STEP 3: VENV1 Result Serialization ===")
        step3_script = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
python -c "
import pickle
import sys
import traceback

print('VENV1 - Serializing result')

try:
    with open('result_venv2.pkl', 'rb') as f:
        result = pickle.load(f)
    
    print('Loaded result from VENV2:', result)
    
    with open('result.pkl', 'wb') as f:
        pickle.dump(result, f, protocol=4)
        
    print('Result serialized successfully')
    
except Exception as e:
    print('VENV1 - Error during result serialization:', str(e))
    traceback.print_exc()
    with open('error.pkl', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f, protocol=4)
    raise
"
"""

        stdin, stdout, stderr = ssh_client.exec_command(step3_script)
        step3_output = stdout.read().decode().strip()
        step3_error = stderr.read().decode().strip()
        print(f"STEP 3 OUTPUT:\n{step3_output}")
        if step3_error:
            print(f"STEP 3 ERROR:\n{step3_error}")

        # Check final result file
        stdin, stdout, stderr = ssh_client.exec_command(
            f"ls -la '{latest_job_dir}/result.pkl'"
        )
        final_result_check = stdout.read().decode().strip()
        print(f"Final result file: {final_result_check}")

        # Try to read and verify the final result
        print("\n=== FINAL RESULT VERIFICATION ===")
        verify_script = f"""
cd '{latest_job_dir}'
source venv1_serialization/bin/activate
python -c "
import pickle
try:
    with open('result.pkl', 'rb') as f:
        result = pickle.load(f)
    print('Final result:', result)
    print('Result type:', type(result))
    if isinstance(result, dict):
        print('Result keys:', list(result.keys()))
except Exception as e:
    print('Error reading result:', str(e))
"
"""

        stdin, stdout, stderr = ssh_client.exec_command(verify_script)
        verify_output = stdout.read().decode().strip()
        verify_error = stderr.read().decode().strip()
        print(f"VERIFICATION OUTPUT:\n{verify_output}")
        if verify_error:
            print(f"VERIFICATION ERROR:\n{verify_error}")

    ssh_client.close()

    print("\n=== Full Manual Workflow Test Complete ===")
    return True
