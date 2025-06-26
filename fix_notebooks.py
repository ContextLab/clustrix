#!/usr/bin/env python3
"""
Fix Jupyter notebooks by adding missing execution_count fields and ensuring proper structure.
"""

import json
import os
from pathlib import Path

def fix_notebook(notebook_path):
    """Fix a single notebook by adding missing execution_count fields."""
    print(f"Fixing notebook: {notebook_path}")
    
    with open(notebook_path, 'r') as f:
        notebook = json.load(f)
    
    # Ensure notebook has proper structure
    if 'cells' not in notebook:
        print(f"  Warning: No cells found in {notebook_path}")
        return False
    
    modified = False
    
    for i, cell in enumerate(notebook['cells']):
        if cell.get('cell_type') == 'code':
            # Add execution_count if missing
            if 'execution_count' not in cell:
                cell['execution_count'] = None
                modified = True
                print(f"  Added execution_count to cell {i}")
            
            # Ensure outputs exist
            if 'outputs' not in cell:
                cell['outputs'] = []
                modified = True
                print(f"  Added outputs to cell {i}")
            
            # Ensure metadata exists
            if 'metadata' not in cell:
                cell['metadata'] = {}
                modified = True
                print(f"  Added metadata to cell {i}")
        
        # Ensure all cells have id
        if 'id' not in cell:
            cell['id'] = f"cell-{i}"
            modified = True
            print(f"  Added id to cell {i}")
    
    # Ensure notebook metadata exists
    if 'metadata' not in notebook:
        notebook['metadata'] = {}
        modified = True
    
    # Add standard notebook metadata if missing
    if 'kernelspec' not in notebook['metadata']:
        notebook['metadata']['kernelspec'] = {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3"
        }
        modified = True
    
    if 'language_info' not in notebook['metadata']:
        notebook['metadata']['language_info'] = {
            "codemirror_mode": {
                "name": "ipython",
                "version": 3
            },
            "file_extension": ".py",
            "mimetype": "text/x-python",
            "name": "python",
            "nbconvert_exporter": "python",
            "pygments_lexer": "ipython3",
            "version": "3.8.0"
        }
        modified = True
    
    # Ensure nbformat and nbformat_minor exist
    if 'nbformat' not in notebook:
        notebook['nbformat'] = 4
        modified = True
    
    if 'nbformat_minor' not in notebook:
        notebook['nbformat_minor'] = 4
        modified = True
    
    if modified:
        # Write back the fixed notebook
        with open(notebook_path, 'w') as f:
            json.dump(notebook, f, indent=2, ensure_ascii=False)
        print(f"  ✓ Fixed and saved {notebook_path}")
        return True
    else:
        print(f"  ✓ No changes needed for {notebook_path}")
        return False

def main():
    """Fix all notebooks in the docs/source/notebooks directory."""
    notebooks_dir = Path("/Users/jmanning/clustrix/docs/source/notebooks")
    
    if not notebooks_dir.exists():
        print(f"Directory not found: {notebooks_dir}")
        return
    
    notebook_files = list(notebooks_dir.glob("*.ipynb"))
    
    if not notebook_files:
        print(f"No notebook files found in {notebooks_dir}")
        return
    
    print(f"Found {len(notebook_files)} notebook files")
    
    fixed_count = 0
    for notebook_path in notebook_files:
        if fix_notebook(notebook_path):
            fixed_count += 1
    
    print(f"\n✓ Fixed {fixed_count} out of {len(notebook_files)} notebooks")

if __name__ == "__main__":
    main()