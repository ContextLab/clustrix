# Function Dependency Resolution Design

## Overview

This document outlines the design for a comprehensive function dependency resolution system for ClustriX that can handle:

1. **Nested/inline functions** - Functions defined within other functions
2. **Cross-file dependencies** - Functions imported from other local files
3. **Local vs external distinction** - Differentiating between local code and external libraries
4. **Complex edge cases** - Name reuse, recursion, circular dependencies

## Current State Analysis

### Existing Function Flattening (`clustrix/function_flattening.py`)

**Strengths:**
- Detects nested functions in AST analysis (`nested_functions > 0` triggers flattening)
- Has framework for extracting nested functions (`_extract_nested_function`)
- Complexity-based triggering system works

**Limitations:**
- Only does statement-level complexity reduction via subprocess wrapping
- Doesn't actually hoist nested functions to module level
- No cross-file dependency resolution
- No distinction between local vs external functions
- Generated flattened code uses subprocess pattern instead of true flattening

## Proposed Architecture

### 1. Function Dependency Analyzer

```python
class FunctionDependencyAnalyzer:
    """Analyzes function dependencies across the entire codebase."""
    
    def __init__(self, root_dir: str, package_dirs: List[str] = None):
        self.root_dir = root_dir
        self.package_dirs = package_dirs or []
        self.external_packages = set()  # Known external packages
        self.local_modules = {}  # Cache of parsed local modules
        self.dependency_graph = {}  # Function -> dependencies mapping
        
    def analyze_function_dependencies(self, func: Callable) -> DependencyInfo:
        """Analyze all dependencies of a function."""
        pass
        
    def is_local_function(self, func_name: str, module_path: str) -> bool:
        """Determine if a function is local or external."""
        pass
        
    def resolve_cross_file_dependencies(self, func: Callable) -> List[str]:
        """Find all local functions this function depends on."""
        pass
```

### 2. Function Dependency Graph

```python
@dataclass
class FunctionNode:
    """Represents a function in the dependency graph."""
    name: str
    source_code: str
    module_path: str
    is_nested: bool
    is_local: bool
    dependencies: List[str]
    closure_vars: List[str]  # Variables captured from outer scope
    
@dataclass 
class DependencyInfo:
    """Complete dependency information for a function."""
    main_function: FunctionNode
    dependencies: List[FunctionNode]  # All required functions
    modules_to_import: List[str]      # External modules needed
    global_variables: Dict[str, Any]  # Global vars to preserve
    circular_dependencies: List[Tuple[str, str]]  # Detected cycles
```

### 3. Function Flattening Engine

```python
class AdvancedFunctionFlattener:
    """Advanced function flattening with full dependency resolution."""
    
    def __init__(self, dependency_analyzer: FunctionDependencyAnalyzer):
        self.analyzer = dependency_analyzer
        
    def flatten_with_dependencies(self, func: Callable) -> FlattenedFunction:
        """Flatten function and all its local dependencies."""
        
        # 1. Analyze dependencies
        dep_info = self.analyzer.analyze_function_dependencies(func)
        
        # 2. Detect and resolve circular dependencies
        if dep_info.circular_dependencies:
            return self._handle_circular_dependencies(dep_info)
            
        # 3. Topologically sort dependencies
        sorted_deps = self._topological_sort(dep_info.dependencies)
        
        # 4. Generate flattened code
        return self._generate_flattened_code(dep_info, sorted_deps)
        
    def _hoist_nested_functions(self, func_node: FunctionNode) -> List[FunctionNode]:
        """Extract nested functions and hoist to module level."""
        pass
        
    def _resolve_closure_variables(self, func_node: FunctionNode) -> str:
        """Resolve closure variable dependencies."""
        pass
```

## Implementation Strategy

### Phase 1: Local vs External Function Detection

**Approach:**
- Use `inspect.getfile()` to get function source file
- Compare against known external package locations (`site-packages`, etc.)
- Build whitelist of known external packages (`torch`, `numpy`, etc.)
- Build blacklist of local project directories

**Implementation:**
```python
def is_external_function(func: Callable) -> bool:
    """Determine if function is from external package."""
    try:
        func_file = inspect.getfile(func)
        
        # Check if in site-packages or other external locations
        external_indicators = [
            'site-packages',
            'dist-packages', 
            '/usr/lib/python',
            '/System/Library',
            'conda/envs'
        ]
        
        return any(indicator in func_file for indicator in external_indicators)
    except (TypeError, OSError):
        # Built-in functions, C extensions, etc.
        return True
```

### Phase 2: AST-Based Dependency Analysis

**Function Call Detection:**
```python
class FunctionCallVisitor(ast.NodeVisitor):
    """Find all function calls and imports in AST."""
    
    def visit_Call(self, node):
        # Extract function name and module
        if isinstance(node.func, ast.Name):
            self.function_calls.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            # Handle module.function calls
            self.attribute_calls.append(self._extract_full_name(node.func))
            
    def visit_Import(self, node):
        # Track imports for dependency resolution
        pass
        
    def visit_ImportFrom(self, node):
        # Track from imports
        pass
```

### Phase 3: Nested Function Hoisting

**Strategy:**
1. Parse function AST to find all nested function definitions
2. Extract nested functions with their closure dependencies
3. Convert closure variables to explicit parameters
4. Hoist to module level with unique names
5. Rewrite calling code to use hoisted functions

**Example Transformation:**
```python
# Original
def outer(x):
    def inner(y):
        return x + y  # Uses closure variable 'x'
    return inner(5)

# Flattened
def outer_inner_hoisted(x, y):  # 'x' becomes parameter
    return x + y

def outer_flattened(x):
    return outer_inner_hoisted(x, 5)  # Pass 'x' explicitly
```

### Phase 4: Cross-File Dependency Resolution

**Module Discovery:**
```python
def find_local_modules(root_dir: str) -> Dict[str, ast.Module]:
    """Find and parse all local Python modules."""
    modules = {}
    
    for file_path in glob.glob(f"{root_dir}/**/*.py", recursive=True):
        # Skip __pycache__, tests, etc.
        if should_include_module(file_path):
            try:
                with open(file_path, 'r') as f:
                    source = f.read()
                modules[file_path] = ast.parse(source)
            except SyntaxError:
                continue  # Skip unparseable files
                
    return modules
```

**Function Resolution:**
```python
def resolve_function_definition(func_name: str, modules: Dict[str, ast.Module]) -> Optional[FunctionNode]:
    """Find function definition across all local modules."""
    
    for module_path, module_ast in modules.items():
        for node in ast.walk(module_ast):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return FunctionNode(
                    name=func_name,
                    source_code=ast.unparse(node),
                    module_path=module_path,
                    is_nested=False,
                    is_local=True,
                    dependencies=[],
                    closure_vars=[]
                )
    return None
```

## Edge Cases and Solutions

### 1. Name Reuse/Shadowing
**Problem:** Same function name in different modules or scopes
**Solution:** Use fully qualified names with module paths

### 2. Circular Dependencies  
**Problem:** Function A calls B, B calls A
**Solution:** Detect cycles, merge into single flattened unit

### 3. Dynamic Function Creation
**Problem:** Functions created at runtime
**Solution:** Conservative fallback to subprocess pattern

### 4. Closure Variables
**Problem:** Nested functions capture variables from outer scope
**Solution:** Convert to explicit parameters, pass values through call chain

### 5. Recursive Functions
**Problem:** Function calls itself
**Solution:** Preserve recursion in flattened form, no additional hoisting needed

## Testing Strategy

### Test Categories

1. **Basic Nested Functions**
   - Simple nested function
   - Multiple nested functions
   - Deeply nested (3+ levels)

2. **Closure Variables**
   - Simple closure capture
   - Multiple closure variables
   - Complex closure patterns

3. **Cross-File Dependencies**
   - Import from other local module
   - Multiple cross-file dependencies
   - Dependency chains across files

4. **Edge Cases**
   - Circular dependencies
   - Name shadowing
   - Recursive functions
   - Dynamic imports

5. **Integration Tests**
   - Full end-to-end flattening
   - GPU computation with flattening
   - Performance benchmarks

## Implementation Timeline

**Week 1:** Core dependency analyzer and local vs external detection
**Week 2:** Nested function hoisting and closure resolution  
**Week 3:** Cross-file dependency resolution
**Week 4:** Edge case handling and comprehensive testing
**Week 5:** Integration with existing ClustriX system

## Success Criteria

1. **Functional:** All nested/inline functions can be automatically flattened
2. **Correctness:** Flattened functions produce identical results to originals
3. **Robustness:** Handles edge cases gracefully with clear error messages
4. **Performance:** Flattening adds <1s overhead for typical functions
5. **Maintainability:** Clean, well-tested code with comprehensive documentation

## Open Questions

1. **Scope:** Should we analyze entire project or just function's immediate module?
2. **Caching:** How to cache dependency analysis results for performance?
3. **Version Control:** How to handle code changes during long-running jobs?
4. **Error Handling:** What level of fallback is acceptable when flattening fails?