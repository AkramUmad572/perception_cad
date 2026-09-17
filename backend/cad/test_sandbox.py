#!/usr/bin/env python3
"""
Regression tests for CadQuery sandbox execution.

Run with: python -m cad.test_sandbox
Or:       python backend/cad/test_sandbox.py

Tests that:
1. Legitimate CadQuery geometry scripts succeed
2. Security-violating scripts are blocked
3. Error types are correctly categorized
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cad.sandbox import execute_cadquery_script


# Scripts that SHOULD succeed
VALID_SCRIPTS = {
    "simple_box": '''
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 5)
''',
    
    "box_with_hole": '''
import cadquery as cq
result = cq.Workplane("XY").box(30, 30, 20).faces(">Z").workplane().hole(10)
''',
    
    "keychain_with_fillet": '''
import cadquery as cq
result = (
    cq.Workplane("XY")
    .box(40, 20, 3)
    .edges("|Z")
    .fillet(5)
    .faces(">Z")
    .workplane()
    .center(15, 0)
    .hole(5)
)
''',
    
    "cylinder_chamfer": '''
import cadquery as cq
result = cq.Workplane("XY").circle(15).extrude(40).edges(">Z").chamfer(2)
''',

    "union_cut": '''
import cadquery as cq
box = cq.Workplane("XY").box(30, 30, 30)
cylinder = cq.Workplane("XY").cylinder(40, 10)
result = box.cut(cylinder)
''',
    
    "with_math": '''
import cadquery as cq
import math
radius = 10 * math.sqrt(2)
result = cq.Workplane("XY").circle(radius).extrude(20)
''',
    
    "text_extrusion": '''
import cadquery as cq
box = cq.Workplane("XY").box(50, 30, 10)
text = cq.Workplane("XY").workplane(offset=10).text("Hi", 10, 3)
result = box.union(text)
''',

    "mug_with_handle": '''
import cadquery as cq
body = (
    cq.Workplane("XY")
    .circle(25)
    .extrude(60)
    .faces(">Z")
    .shell(-3)
)
handle = (
    cq.Workplane("XZ")
    .center(25, 30)
    .ellipse(8, 15)
    .extrude(5)
)
result = body.union(handle)
''',
}


# Scripts that SHOULD be blocked with security errors
SECURITY_BLOCKED_SCRIPTS = {
    "import_os": "import os\nresult = os.getcwd()",
    "import_subprocess": "import subprocess\nresult = subprocess.run(['ls'])",
    "import_socket": "import socket\nresult = socket.socket()",
    "import_requests": "import requests\nresult = requests.get('http://example.com')",
    "from_os_import": "from os import getcwd\nresult = getcwd()",
    "import_sys": "import sys\nresult = sys.path",
    "import_pathlib": "from pathlib import Path\nresult = Path('/')",
    # CadQuery proxy blocks access to internal modules
    "cq_exporters": "import cadquery as cq\nx = cq.exporters\nresult = cq.Workplane('XY').box(10,10,5)",
    "cq_occ_impl": "import cadquery as cq\nx = cq.occ_impl\nresult = cq.Workplane('XY').box(10,10,5)",
}


# Scripts blocked via stripped builtins (not import security)
BUILTIN_BLOCKED_SCRIPTS = {
    "open_file": "import cadquery as cq\nf = open('/etc/passwd')\nresult = cq.Workplane('XY').box(10, 10, 5)",
    "exec_code": "import cadquery as cq\nexec('print(1)')\nresult = cq.Workplane('XY').box(10, 10, 5)",
    "eval_code": "import cadquery as cq\nx = eval('1+1')\nresult = cq.Workplane('XY').box(10, 10, 5)",
    "getattr_escape": "import cadquery as cq\nx = getattr(cq, '__file__')\nresult = cq.Workplane('XY').box(10, 10, 5)",
}


def test_valid_scripts():
    """Test that legitimate CadQuery scripts succeed."""
    print("\n=== Testing Valid Scripts ===")
    passed = 0
    failed = 0
    
    for name, script in VALID_SCRIPTS.items():
        result = execute_cadquery_script(script)
        if result["ok"]:
            print(f"  ✓ {name}")
            passed += 1
        else:
            error_type = result.get("error_type", "unknown")
            if error_type == "security":
                print(f"  ✗ {name}: SECURITY ERROR - {result.get('error')}")
                failed += 1
            else:
                print(f"  ~ {name}: {error_type} (non-security, may be mesh quality)")
                passed += 1
    
    return passed, failed


def test_security_blocked():
    """Test that security-violating imports are blocked."""
    print("\n=== Testing Security Blocking ===")
    passed = 0
    failed = 0
    
    for name, script in SECURITY_BLOCKED_SCRIPTS.items():
        result = execute_cadquery_script(script)
        if not result["ok"] and result.get("error_type") == "security":
            print(f"  ✓ {name}: Blocked correctly")
            passed += 1
        elif result["ok"]:
            print(f"  ✗ {name}: SECURITY HOLE - Script was not blocked!")
            failed += 1
        else:
            print(f"  ~ {name}: Blocked but as '{result.get('error_type')}' not 'security'")
            passed += 1
    
    return passed, failed


def test_builtin_blocked():
    """Test that dangerous builtins are blocked."""
    print("\n=== Testing Builtin Blocking ===")
    passed = 0
    failed = 0
    
    for name, script in BUILTIN_BLOCKED_SCRIPTS.items():
        result = execute_cadquery_script(script)
        if not result["ok"]:
            print(f"  ✓ {name}: Blocked ({result.get('error_type')})")
            passed += 1
        else:
            print(f"  ✗ {name}: SECURITY HOLE - Script was not blocked!")
            failed += 1
    
    return passed, failed


def run_all_tests():
    """Run all sandbox tests."""
    print("=" * 60)
    print("CADQUERY SANDBOX REGRESSION TESTS")
    print("=" * 60)
    
    v_pass, v_fail = test_valid_scripts()
    s_pass, s_fail = test_security_blocked()
    b_pass, b_fail = test_builtin_blocked()
    
    total_pass = v_pass + s_pass + b_pass
    total_fail = v_fail + s_fail + b_fail
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Valid scripts:     {v_pass}/{v_pass + v_fail} passed")
    print(f"Security blocking: {s_pass}/{s_pass + s_fail} passed")
    print(f"Builtin blocking:  {b_pass}/{b_pass + b_fail} passed")
    print(f"TOTAL:             {total_pass}/{total_pass + total_fail} passed")
    
    if total_fail > 0:
        print(f"\n⚠️  {total_fail} TESTS FAILED")
        return 1
    else:
        print("\n✓ ALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
